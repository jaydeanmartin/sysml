package DroneArchitectures {
    private import ScalarValues::*;
    private import ISQ::*;
    private import SequenceFunctions::*;
    private import NumericalFunctions::*;
    private import DroneLibrary::*;

    // ---------------------------------------------------------- common architecture
    // Not tagged #SystemArchitecture itself: the quad, hexa and octo layouts
    // below are the architectures. They differ in the number of rotors, i.e.
    // the multiplicity of the motors and propellers slots.
    abstract part def DeliveryDrone {
        doc /* Multirotor parcel delivery drone. All rotors use the same motor and propeller. */
        part airframe : Frame;
        part battery : Battery;
        part motors : Motor[1..*];
        part propellers : Propeller[1..*];
        part payload : PayloadModule;
        part controller : FlightController;
        connect battery.powerOut to motors.powerIn;
        connect motors.shaft to propellers.hub;
        connect airframe.payloadMount to payload.mount;

        // behavior slots: each system instance performs a behavior instance
        perform action takeoff : Takeoff;
        perform action cruise : Cruise;
        perform action hover : Hover;
        perform action land : Land;
        perform action release : GroundRelease;
        perform action turn : Turn;

        // sin(pi / rotors): the gap between adjacent motors is wheelbase * spacing
        attribute spacing : Real;

        attribute rotorCount : Integer = size(motors);
        attribute payloadCapacity : MassValue = payload.capacity;
        attribute emptyMass : MassValue = airframe.hubMass + airframe.armMass * rotorCount + sum(motors.mass) + sum(propellers.mass)
            + battery.mass + payload.mass + 0.5 [SI::kg];   // 0.5 kg avionics: flight controller, GPS, radio
        attribute takeoffMass : MassValue = emptyMass + payload.capacity;
        // thrust per watt at full throttle is about half its hover value
        attribute maxThrust : Real = rotorCount * motors.maxPower#(1) * motors.efficiency#(1) * propellers.thrustPerWatt#(1) * 0.5;   // N
        attribute thrustToWeight : Real = maxThrust / (takeoffMass / (1 [SI::kg]) * 9.81);
        attribute hoverPower : Real = takeoffMass / (1 [SI::kg]) * 9.81 / (propellers.thrustPerWatt#(1) * motors.efficiency#(1));   // W
        attribute endurance : Real = battery.energy * 0.8 * 60 / hoverPower;   // min of hover, 20 % reserve
        attribute peakPower : Real = rotorCount * motors.maxPower#(1);        // W
        // rigid-body inertia for the control simulation (closed form, X layout):
        // rotors and arms on a circle of armLength, the hub a disc, battery and
        // parcel point masses 5 and 10 cm off the rotor plane; rotor inertia from a
        // propeller (a rod) and the motor bell (35 % of its mass on a radius
        // 1.5 mm per inch of the largest propeller it takes)
        attribute armLength : Real = airframe.wheelbase / 2000;     // m
        attribute rotorMass : Real = (motors.mass#(1) + propellers.mass#(1)) / (1 [SI::kg]);
        attribute hubRadius : Real = 0.16 * airframe.wheelbase / 1000;
        attribute inertiaZZ : Real = rotorCount * rotorMass * armLength * armLength
            + rotorCount * airframe.armMass / (1 [SI::kg]) * armLength * armLength / 3
            + airframe.hubMass / (1 [SI::kg]) * hubRadius * hubRadius / 2;     // kg·m², yaw
        attribute inertiaXX : Real = inertiaZZ / 2 + battery.mass / (1 [SI::kg]) * 0.0025
            + (payload.mass + payload.capacity) / (1 [SI::kg]) * 0.01;            // kg·m², roll and pitch
        attribute rotorInertia : Real = propellers.mass#(1) / (1 [SI::kg]) * (propellers.diameter#(1) * 0.0254) * (propellers.diameter#(1) * 0.0254) / 12
            + 0.35 * motors.mass#(1) / (1 [SI::kg]) * (0.0015 * motors.maxPropDiameter#(1)) * (0.0015 * motors.maxPropDiameter#(1));   // kg·m²
        attribute firstRotorSpin : Integer = 1;    // rotor 1 counter-clockwise seen from above; the others alternate
        attribute propClearance : Real = airframe.wheelbase * spacing - propellers.diameter#(1) * 25.4;   // mm between tips
    }

    #SystemArchitecture part def Quadcopter :> DeliveryDrone {
        doc /* Four rotors: lightest and simplest, no motor-out redundancy. */
        part :>> motors[4];
        part :>> propellers[4];
        attribute :>> spacing = 0.7071;
    }
    #SystemArchitecture part def Hexacopter :> DeliveryDrone {
        doc /* Six rotors: can land after losing one motor. */
        part :>> motors[6];
        part :>> propellers[6];
        attribute :>> spacing = 0.5;
    }
    #SystemArchitecture part def Octocopter :> DeliveryDrone {
        doc /* Eight rotors: heavy lift with motor-out redundancy; lowers parcels on a winch. */
        part :>> motors[8];
        perform action winch : WinchLower;
        part :>> propellers[8];
        attribute :>> spacing = 0.3827;
    }

    // ---------------------------------------------------------- requirements
    // In the DroneRequirements model: requirement types with parameters, and the
    // specification of instances they are checked with.

    // ---------------------------------------------------------- system instances
    // Created and edited from the Configurator tab.
    package Configurations {
        part courier : Quadcopter {
            doc /* Small parcels over short hops: marginal thrust, and too little battery for 15 min */
            part :>> airframe :> DroneLibrary::Catalog::frame450;
            part :>> battery :> DroneLibrary::Catalog::lipo4s8000;
            part :>> motors :> DroneLibrary::Catalog::m2814;
            part :>> propellers :> DroneLibrary::Catalog::prop10x4_5;
            part :>> payload :> DroneLibrary::Catalog::box1kg;
            part :>> controller :> DroneLibrary::Catalog::fcCourier;
            perform action :>> takeoff :> DroneLibrary::Behaviors::takeoffStandard;
            perform action :>> cruise :> DroneLibrary::Behaviors::cruise15;
            perform action :>> hover :> DroneLibrary::Behaviors::holdPosition;
            perform action :>> land :> DroneLibrary::Behaviors::landStandard;
            perform action :>> release :> DroneLibrary::Behaviors::releaseIdle;
            perform action :>> turn :> DroneLibrary::Behaviors::turnStandard;
        }
        part parcel : Quadcopter {
            doc /* Standard 2 kg parcel delivery */
            part :>> airframe :> DroneLibrary::Catalog::frame700;
            part :>> battery :> DroneLibrary::Catalog::lipo6s10000;
            part :>> motors :> DroneLibrary::Catalog::m4114;
            part :>> propellers :> DroneLibrary::Catalog::prop15x5;
            part :>> payload :> DroneLibrary::Catalog::box2kg;
            part :>> controller :> DroneLibrary::Catalog::fcParcel;
            perform action :>> takeoff :> DroneLibrary::Behaviors::takeoffStandard;
            perform action :>> cruise :> DroneLibrary::Behaviors::cruise12;
            perform action :>> hover :> DroneLibrary::Behaviors::holdPosition;
            perform action :>> land :> DroneLibrary::Behaviors::landStandard;
            perform action :>> release :> DroneLibrary::Behaviors::releaseIdle;
            perform action :>> turn :> DroneLibrary::Behaviors::turnStandard;
        }
        part parcelLongRange : Quadcopter {
            doc /* The parcel drone with the big battery: flies longer, but too heavy for its motors */
            part :>> airframe :> DroneLibrary::Catalog::frame700;
            part :>> battery :> DroneLibrary::Catalog::lipo6s22000;
            part :>> motors :> DroneLibrary::Catalog::m4114;
            part :>> propellers :> DroneLibrary::Catalog::prop15x5;
            part :>> payload :> DroneLibrary::Catalog::box2kg;
            part :>> controller :> DroneLibrary::Catalog::fcParcelLongRange;
            perform action :>> takeoff :> DroneLibrary::Behaviors::takeoffStandard;
            perform action :>> cruise :> DroneLibrary::Behaviors::cruise12;
            perform action :>> hover :> DroneLibrary::Behaviors::holdPosition;
            perform action :>> land :> DroneLibrary::Behaviors::landStandard;
            perform action :>> release :> DroneLibrary::Behaviors::releaseIdle;
            perform action :>> turn :> DroneLibrary::Behaviors::turnStandard;
        }
        part regional : Hexacopter {
            doc /* 5 kg parcels, motor-out redundancy */
            part :>> airframe :> DroneLibrary::Catalog::frame1200;
            part :>> battery :> DroneLibrary::Catalog::lipo6s22000;
            part :>> motors :> DroneLibrary::Catalog::m6215;
            part :>> propellers :> DroneLibrary::Catalog::prop22x7;
            part :>> payload :> DroneLibrary::Catalog::box5kg;
            part :>> controller :> DroneLibrary::Catalog::fcRegional;
            perform action :>> takeoff :> DroneLibrary::Behaviors::takeoffStandard;
            perform action :>> cruise :> DroneLibrary::Behaviors::cruise12;
            perform action :>> hover :> DroneLibrary::Behaviors::holdPosition;
            perform action :>> land :> DroneLibrary::Behaviors::landStandard;
            perform action :>> release :> DroneLibrary::Behaviors::releaseIdle;
            perform action :>> turn :> DroneLibrary::Behaviors::turnStandard;
        }
        part regionalLiIon : Hexacopter {
            doc /* Li-ion pack for endurance: the pack cannot deliver full-throttle power */
            part :>> airframe :> DroneLibrary::Catalog::frame1200;
            part :>> battery :> DroneLibrary::Catalog::liion6s30000;
            part :>> motors :> DroneLibrary::Catalog::m6215;
            part :>> propellers :> DroneLibrary::Catalog::prop22x7;
            part :>> payload :> DroneLibrary::Catalog::box5kg;
            part :>> controller :> DroneLibrary::Catalog::fcRegionalLiIon;
            perform action :>> takeoff :> DroneLibrary::Behaviors::takeoffStandard;
            perform action :>> cruise :> DroneLibrary::Behaviors::cruise10;
            perform action :>> hover :> DroneLibrary::Behaviors::holdPosition;
            perform action :>> land :> DroneLibrary::Behaviors::landGentle;
            perform action :>> release :> DroneLibrary::Behaviors::releaseIdle;
            perform action :>> turn :> DroneLibrary::Behaviors::turnStandard;
        }
        part heavyLift : Octocopter {
            doc /* 10 kg winch delivery */
            part :>> airframe :> DroneLibrary::Catalog::frame1600;
            part :>> battery :> DroneLibrary::Catalog::lipo12s22000;
            part :>> motors :> DroneLibrary::Catalog::m6215;
            part :>> propellers :> DroneLibrary::Catalog::prop22x7;
            part :>> payload :> DroneLibrary::Catalog::winch10kg;
            part :>> controller :> DroneLibrary::Catalog::fcHeavyLift;
            perform action :>> takeoff :> DroneLibrary::Behaviors::takeoffGentle;
            perform action :>> cruise :> DroneLibrary::Behaviors::cruise10;
            perform action :>> hover :> DroneLibrary::Behaviors::holdPosition;
            perform action :>> land :> DroneLibrary::Behaviors::landGentle;
            perform action :>> release :> DroneLibrary::Behaviors::releaseIdle;
            perform action :>> turn :> DroneLibrary::Behaviors::turnGentle;
            perform action :>> winch :> DroneLibrary::Behaviors::winch1;
        }
    }
}
