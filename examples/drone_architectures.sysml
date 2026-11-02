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
        connect battery.powerOut to motors.powerIn;
        connect motors.shaft to propellers.hub;
        connect airframe.payloadMount to payload.mount;

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
        doc /* Eight rotors: heavy lift with motor-out redundancy. */
        part :>> motors[8];
        part :>> propellers[8];
        attribute :>> spacing = 0.3827;
    }

    // ---------------------------------------------------------- requirements
    // Checked for every system instance by the Configurator (subject: DeliveryDrone).
    requirement def ThrustToWeightRequirement {
        doc /* Full-throttle thrust shall be at least twice the weight at maximum takeoff mass. */
        subject drone : DeliveryDrone;
        require constraint { drone.thrustToWeight >= 2.0 }
    }
    requirement def EnduranceRequirement {
        doc /* The drone shall hover for at least 15 minutes with a full payload, keeping a 20 % battery reserve. */
        subject drone : DeliveryDrone;
        require constraint { drone.endurance >= 15 }
    }
    requirement def FrameLoadRequirement {
        doc /* The takeoff mass shall not exceed the frame's rated maximum takeoff mass. */
        subject drone : DeliveryDrone;
        require constraint { drone.takeoffMass <= drone.airframe.maxTakeoffMass }
    }
    requirement def PropellerClearanceRequirement {
        doc /* Adjacent propeller tips shall not overlap. */
        subject drone : DeliveryDrone;
        require constraint { drone.propClearance >= 0 }
    }
    requirement def PropellerSizeRequirement {
        doc /* The propeller shall be no larger than the motor's largest rated propeller. */
        subject drone : DeliveryDrone;
        require constraint { drone.propellers.diameter#(1) <= drone.motors.maxPropDiameter#(1) }
    }
    requirement def BatteryPowerRequirement {
        doc /* The battery shall supply every motor at full throttle. */
        subject drone : DeliveryDrone;
        require constraint { drone.peakPower <= drone.battery.maxPower }
    }

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
        }
        part parcel : Quadcopter {
            doc /* Standard 2 kg parcel delivery */
            part :>> airframe :> DroneLibrary::Catalog::frame700;
            part :>> battery :> DroneLibrary::Catalog::lipo6s10000;
            part :>> motors :> DroneLibrary::Catalog::m4114;
            part :>> propellers :> DroneLibrary::Catalog::prop15x5;
            part :>> payload :> DroneLibrary::Catalog::box2kg;
        }
        part parcelLongRange : Quadcopter {
            doc /* The parcel drone with the big battery: flies longer, but too heavy for its motors */
            part :>> airframe :> DroneLibrary::Catalog::frame700;
            part :>> battery :> DroneLibrary::Catalog::lipo6s22000;
            part :>> motors :> DroneLibrary::Catalog::m4114;
            part :>> propellers :> DroneLibrary::Catalog::prop15x5;
            part :>> payload :> DroneLibrary::Catalog::box2kg;
        }
        part regional : Hexacopter {
            doc /* 5 kg parcels, motor-out redundancy */
            part :>> airframe :> DroneLibrary::Catalog::frame1200;
            part :>> battery :> DroneLibrary::Catalog::lipo6s22000;
            part :>> motors :> DroneLibrary::Catalog::m6215;
            part :>> propellers :> DroneLibrary::Catalog::prop22x7;
            part :>> payload :> DroneLibrary::Catalog::box5kg;
        }
        part regionalLiIon : Hexacopter {
            doc /* Li-ion pack for endurance: the pack cannot deliver full-throttle power */
            part :>> airframe :> DroneLibrary::Catalog::frame1200;
            part :>> battery :> DroneLibrary::Catalog::liion6s30000;
            part :>> motors :> DroneLibrary::Catalog::m6215;
            part :>> propellers :> DroneLibrary::Catalog::prop22x7;
            part :>> payload :> DroneLibrary::Catalog::box5kg;
        }
        part heavyLift : Octocopter {
            doc /* 10 kg winch delivery */
            part :>> airframe :> DroneLibrary::Catalog::frame1600;
            part :>> battery :> DroneLibrary::Catalog::lipo12s22000;
            part :>> motors :> DroneLibrary::Catalog::m6215;
            part :>> propellers :> DroneLibrary::Catalog::prop22x7;
            part :>> payload :> DroneLibrary::Catalog::winch10kg;
        }
    }
}
