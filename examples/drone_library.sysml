package DroneLibrary {
    private import ScalarValues::*;
    private import ISQ::*;

    // ---------------------------------------------------------- markers
    // The Library and Configurator tabs look for these metadata tags.
    metadata def Component {
        doc /* A reusable component type. Its instances form the component catalog. */
    }
    metadata def SystemArchitecture {
        doc /* A system-level part def. Its part usages are slots that component instances fill, its performed actions behavior slots that behavior instances fill. */
    }
    metadata def SystemSimulation {
        doc /* A simulation of a system instance on its own (not of a mission), run from the Configurator. */
    }
    metadata def Behavior {
        doc /* An abstract behavior: its in parameters are set by the system that performs it, its quantities by each mission step. */
    }

    // ---------------------------------------------------------- interfaces
    port def PowerPort {
        doc /* DC power from the battery to a motor's speed controller. */
        out attribute voltage : Real;       // V
    }
    port def ShaftPort {
        doc /* Motor shaft driving a propeller. */
        out attribute speed : Real;         // rpm
    }
    port def MountPort {
        doc /* Mechanical mount on the frame for a payload module. */
    }

    // ---------------------------------------------------------- component types
    #Component part def Frame {
        doc /* Airframe: a central hub and one arm per rotor. The wheelbase is the motor-to-motor diagonal. */
        attribute hubMass : MassValue;
        attribute armMass : MassValue;      // per arm
        attribute wheelbase : Real;         // mm
        attribute maxTakeoffMass : MassValue;
        attribute dragArea : Real;          // m², drag coefficient × frontal area (dynamic simulation)
        port payloadMount : MountPort;
    }

    #Component part def Battery {
        doc /* Flight battery pack. */
        attribute mass : MassValue;
        attribute voltage : Real;           // V (nominal)
        attribute energy : Real;            // Wh
        attribute maxPower : Real;          // W (continuous discharge)
        port powerOut : PowerPort;
    }

    #Component part def Motor {
        doc /* Brushless outrunner motor with its speed controller. */
        attribute mass : MassValue;
        attribute maxPower : Real;          // W (electrical, at full throttle)
        attribute efficiency : Real;        // motor and controller
        attribute maxPropDiameter : Real;   // in
        // brushless DC model (dynamic simulation): speed, torque, power and efficiency
        // follow from I = torque * Kv + I0 and V = speed / Kv + I * R
        attribute kv : Real;                // rpm/V
        attribute resistance : Real;        // Ω, winding
        attribute noLoadCurrent : Real;     // A
        attribute maxCurrent : Real;        // A
        port powerIn : ~PowerPort;
        port shaft : ShaftPort;
    }

    #Component part def Propeller {
        doc /* Fixed-pitch propeller. */
        attribute mass : MassValue;
        attribute diameter : Real;          // in
        attribute pitch : Real;             // in
        attribute thrustPerWatt : Real;     // N per W of shaft power, at hover
        // performance curves against the advance ratio J = V / (n D) (dynamic simulation):
        // thrust coefficient CT = ct0 + ct1 J + ct2 J², power coefficient CP = cp0 + cp1 J + cp2 J²;
        // torque coefficient CQ = CP / 2π, efficiency η = J CT / CP
        attribute ct0 : Real;
        attribute ct1 : Real;
        attribute ct2 : Real;
        attribute cp0 : Real;
        attribute cp1 : Real;
        attribute cp2 : Real;
        port hub : ~ShaftPort;
    }

    abstract #Component part def PayloadModule {
        doc /* Anything that carries the parcel; a slot typed by it accepts every kind below. */
        attribute mass : MassValue;
        attribute capacity : MassValue;     // heaviest parcel it carries
        port mount : ~MountPort;
    }
    #Component part def ParcelBox :> PayloadModule {
        doc /* Closed box released on the ground after landing. */
        attribute volume : Real;            // litres
    }
    #Component part def Winch :> PayloadModule {
        doc /* Tether winch that lowers the parcel while the drone hovers. */
        attribute tetherLength : Real;      // m
    }

    #Component part def FlightController {
        doc /* Cascaded flight-control loops and their gains: ESC speed (PI), body rates (PID roll and pitch, PI yaw), attitude and heading (P), velocity and vertical speed (PI), position (P). Tuned by the ControllerTuning analysis. */
        attribute rpmKp : Real;                 // V per rad/s, ESC speed loop
        attribute rpmKi : Real;                 // V per rad
        attribute rateKp : Real;                // N·m per rad/s, roll and pitch rate
        attribute rateKi : Real;                // N·m per rad
        attribute rateKd : Real;                // N·m per rad/s²
        attribute yawRateKp : Real;             // N·m per rad/s
        attribute yawRateKi : Real;             // N·m per rad
        attribute attitudeKp : Real;            // 1/s
        attribute headingKp : Real;             // 1/s
        attribute velocityKp : Real;            // 1/s
        attribute velocityKi : Real;            // 1/s²
        attribute verticalKp : Real;            // 1/s
        attribute verticalKi : Real;            // 1/s²
        attribute positionKp : Real;            // 1/s
        attribute rpmLoop : Boolean default = true;     // the ESCs hold the commanded speed; false: throttle only
        attribute maxTilt : Real default = 30;          // deg
        attribute maxYawRate : Real default = 90;       // deg/s
    }

    // ---------------------------------------------------------- catalog
    // Component instances: each one is a part usage that fixes the attribute
    // values of its component type. The Library tab creates and edits them.
    package Catalog {
        // ------------------------------------------ structures (frame sizes)
        part frame450 : Frame {
            doc /* 450 mm carbon frame, small parcels */
            attribute :>> hubMass = 0.35 [SI::kg];
            attribute :>> armMass = 0.05 [SI::kg];
            attribute :>> wheelbase = 450;
            attribute :>> dragArea = 0.03;
            attribute :>> maxTakeoffMass = 5 [SI::kg];
        }
        part frame700 : Frame {
            doc /* 700 mm carbon frame */
            attribute :>> hubMass = 0.8 [SI::kg];
            attribute :>> armMass = 0.12 [SI::kg];
            attribute :>> wheelbase = 700;
            attribute :>> dragArea = 0.05;
            attribute :>> maxTakeoffMass = 12 [SI::kg];
        }
        part frame1200 : Frame {
            doc /* 1200 mm folding frame */
            attribute :>> hubMass = 1.7 [SI::kg];
            attribute :>> armMass = 0.26 [SI::kg];
            attribute :>> wheelbase = 1200;
            attribute :>> dragArea = 0.12;
            attribute :>> maxTakeoffMass = 22 [SI::kg];
        }
        part frame1600 : Frame {
            doc /* 1600 mm heavy-lift frame */
            attribute :>> hubMass = 2.8 [SI::kg];
            attribute :>> armMass = 0.4 [SI::kg];
            attribute :>> wheelbase = 1600;
            attribute :>> dragArea = 0.2;
            attribute :>> maxTakeoffMass = 35 [SI::kg];
        }

        // ------------------------------------------ batteries
        part lipo4s5000 : Battery {
            doc /* 4S 5000 mAh LiPo */
            attribute :>> mass = 0.55 [SI::kg];
            attribute :>> voltage = 14.8;
            attribute :>> energy = 74;
            attribute :>> maxPower = 2200;
        }
        part lipo4s8000 : Battery {
            doc /* 4S 8000 mAh LiPo */
            attribute :>> mass = 0.8 [SI::kg];
            attribute :>> voltage = 14.8;
            attribute :>> energy = 118;
            attribute :>> maxPower = 2400;
        }
        part lipo6s10000 : Battery {
            doc /* 6S 10000 mAh LiPo */
            attribute :>> mass = 1.35 [SI::kg];
            attribute :>> voltage = 22.2;
            attribute :>> energy = 222;
            attribute :>> maxPower = 5000;
        }
        part lipo6s22000 : Battery {
            doc /* 6S 22000 mAh LiPo */
            attribute :>> mass = 2.7 [SI::kg];
            attribute :>> voltage = 22.2;
            attribute :>> energy = 488;
            attribute :>> maxPower = 10000;
        }
        part liion6s30000 : Battery {
            doc /* 6S 30000 mAh Li-ion: more energy per kg, much less power */
            attribute :>> mass = 2.9 [SI::kg];
            attribute :>> voltage = 21.6;
            attribute :>> energy = 666;
            attribute :>> maxPower = 3000;
        }
        part lipo12s22000 : Battery {
            doc /* Two 6S 22000 mAh LiPo packs in series */
            attribute :>> mass = 5.6 [SI::kg];
            attribute :>> voltage = 44.4;
            attribute :>> energy = 977;
            attribute :>> maxPower = 20000;
        }

        // ------------------------------------------ motors
        part m2814 : Motor {
            doc /* 2814 900 KV */
            attribute :>> mass = 0.11 [SI::kg];
            attribute :>> maxPower = 500;
            attribute :>> efficiency = 0.8;
            attribute :>> maxPropDiameter = 11;
            attribute :>> kv = 900;
            attribute :>> resistance = 0.06;
            attribute :>> noLoadCurrent = 0.9;
            attribute :>> maxCurrent = 35;
        }
        part m4114 : Motor {
            doc /* 4114 400 KV */
            attribute :>> mass = 0.17 [SI::kg];
            attribute :>> maxPower = 700;
            attribute :>> efficiency = 0.83;
            attribute :>> maxPropDiameter = 15;
            attribute :>> kv = 400;
            attribute :>> resistance = 0.1;
            attribute :>> noLoadCurrent = 0.5;
            attribute :>> maxCurrent = 32;
        }
        part m6215 : Motor {
            doc /* 6215 180 KV */
            attribute :>> mass = 0.42 [SI::kg];
            attribute :>> maxPower = 1500;
            attribute :>> efficiency = 0.85;
            attribute :>> maxPropDiameter = 22;
            attribute :>> kv = 180;
            attribute :>> resistance = 0.07;
            attribute :>> noLoadCurrent = 0.6;
            attribute :>> maxCurrent = 70;
        }
        part m8318 : Motor {
            doc /* 8318 100 KV */
            attribute :>> mass = 0.68 [SI::kg];
            attribute :>> maxPower = 3000;
            attribute :>> efficiency = 0.86;
            attribute :>> maxPropDiameter = 30;
            attribute :>> kv = 100;
            attribute :>> resistance = 0.09;
            attribute :>> noLoadCurrent = 0.7;
            attribute :>> maxCurrent = 70;
        }

        // ------------------------------------------ propellers
        part prop10x4_5 : Propeller {
            attribute :>> mass = 0.018 [SI::kg];
            attribute :>> diameter = 10;
            attribute :>> pitch = 4.5;
            attribute :>> thrustPerWatt = 0.085;
            attribute :>> ct0 = 0.110;
            attribute :>> ct1 = -0.05;
            attribute :>> ct2 = -0.25;
            attribute :>> cp0 = 0.045;
            attribute :>> cp1 = 0;
            attribute :>> cp2 = -0.06;
        }
        part prop15x5 : Propeller {
            attribute :>> mass = 0.035 [SI::kg];
            attribute :>> diameter = 15;
            attribute :>> pitch = 5;
            attribute :>> thrustPerWatt = 0.11;
            attribute :>> ct0 = 0.105;
            attribute :>> ct1 = -0.05;
            attribute :>> ct2 = -0.48;
            attribute :>> cp0 = 0.036;
            attribute :>> cp1 = 0;
            attribute :>> cp2 = -0.07;
        }
        part prop22x7 : Propeller {
            doc /* Carbon fibre */
            attribute :>> mass = 0.085 [SI::kg];
            attribute :>> diameter = 22;
            attribute :>> pitch = 7;
            attribute :>> thrustPerWatt = 0.13;
            attribute :>> ct0 = 0.100;
            attribute :>> ct1 = -0.05;
            attribute :>> ct2 = -0.50;
            attribute :>> cp0 = 0.032;
            attribute :>> cp1 = 0;
            attribute :>> cp2 = -0.07;
        }
        part prop28x9 : Propeller {
            doc /* Carbon fibre */
            attribute :>> mass = 0.14 [SI::kg];
            attribute :>> diameter = 28;
            attribute :>> pitch = 9;
            attribute :>> thrustPerWatt = 0.15;
            attribute :>> ct0 = 0.100;
            attribute :>> ct1 = -0.05;
            attribute :>> ct2 = -0.48;
            attribute :>> cp0 = 0.031;
            attribute :>> cp1 = 0;
            attribute :>> cp2 = -0.065;
        }

        // ------------------------------------------ payload capacities
        part box1kg : ParcelBox {
            attribute :>> mass = 0.15 [SI::kg];
            attribute :>> capacity = 1 [SI::kg];
            attribute :>> volume = 3;
        }
        part box2kg : ParcelBox {
            attribute :>> mass = 0.3 [SI::kg];
            attribute :>> capacity = 2 [SI::kg];
            attribute :>> volume = 8;
        }
        part box5kg : ParcelBox {
            attribute :>> mass = 0.6 [SI::kg];
            attribute :>> capacity = 5 [SI::kg];
            attribute :>> volume = 20;
        }
        part winch10kg : Winch {
            attribute :>> mass = 1.6 [SI::kg];
            attribute :>> capacity = 10 [SI::kg];
            attribute :>> tetherLength = 20;
        }
        part winch15kg : Winch {
            attribute :>> mass = 2.2 [SI::kg];
            attribute :>> capacity = 15 [SI::kg];
            attribute :>> tetherLength = 30;
        }
        part fcCourier : FlightController {
            doc /* Tuned for courier */
            attribute :>> rpmKp = 0.02567;
            attribute :>> rpmKi = 0.5436;
            attribute :>> rateKp = 0.5261;
            attribute :>> rateKi = 2.568;
            attribute :>> rateKd = 0.003104;
            attribute :>> yawRateKp = 0.5907;
            attribute :>> yawRateKi = 2.884;
            attribute :>> attitudeKp = 4.615;
            attribute :>> headingKp = 4.615;
            attribute :>> velocityKp = 1.538;
            attribute :>> velocityKi = 0.5917;
            attribute :>> verticalKp = 2.5;
            attribute :>> verticalKi = 1.562;
            attribute :>> positionKp = 0.3846;
        }
        part fcParcel : FlightController {
            doc /* Tuned for parcel */
            attribute :>> rpmKp = 0.05994;
            attribute :>> rpmKi = 0.9362;
            attribute :>> rateKp = 1.195;
            attribute :>> rateKi = 4.391;
            attribute :>> rateKd = 0.009561;
            attribute :>> yawRateKp = 1.858;
            attribute :>> yawRateKi = 6.829;
            attribute :>> attitudeKp = 3.443;
            attribute :>> headingKp = 3.464;
            attribute :>> velocityKp = 1.148;
            attribute :>> velocityKi = 0.3293;
            attribute :>> verticalKp = 2.5;
            attribute :>> verticalKi = 1.562;
            attribute :>> positionKp = 0.2869;
        }
        part fcParcelLongRange : FlightController {
            doc /* Tuned for parcelLongRange */
            attribute :>> rpmKp = 0.06108;
            attribute :>> rpmKi = 0.8966;
            attribute :>> rateKp = 1.162;
            attribute :>> rateKi = 4.028;
            attribute :>> rateKd = 0.009897;
            attribute :>> yawRateKp = 1.764;
            attribute :>> yawRateKi = 6.113;
            attribute :>> attitudeKp = 3.249;
            attribute :>> headingKp = 3.269;
            attribute :>> velocityKp = 1.083;
            attribute :>> velocityKi = 0.2932;
            attribute :>> verticalKp = 2.5;
            attribute :>> verticalKi = 1.562;
            attribute :>> positionKp = 0.2707;
        }
        part fcRegional : FlightController {
            doc /* Tuned for regional */
            attribute :>> rpmKp = 0.1199;
            attribute :>> rpmKi = 2.349;
            attribute :>> rateKp = 12.8;
            attribute :>> rateKi = 58.14;
            attribute :>> rateKd = 0.0817;
            attribute :>> yawRateKp = 23.84;
            attribute :>> yawRateKi = 108.3;
            attribute :>> attitudeKp = 4.276;
            attribute :>> headingKp = 4.289;
            attribute :>> velocityKp = 1.425;
            attribute :>> velocityKi = 0.5079;
            attribute :>> verticalKp = 2.5;
            attribute :>> verticalKi = 1.562;
            attribute :>> positionKp = 0.3563;
        }
        part fcRegionalLiIon : FlightController {
            doc /* Tuned for regionalLiIon */
            attribute :>> rpmKp = 0.12;
            attribute :>> rpmKi = 2.351;
            attribute :>> rateKp = 12.87;
            attribute :>> rateKi = 58.47;
            attribute :>> rateKd = 0.08209;
            attribute :>> yawRateKp = 23.85;
            attribute :>> yawRateKi = 108.4;
            attribute :>> attitudeKp = 4.276;
            attribute :>> headingKp = 4.302;
            attribute :>> velocityKp = 1.425;
            attribute :>> velocityKi = 0.5079;
            attribute :>> verticalKp = 2.5;
            attribute :>> verticalKi = 1.562;
            attribute :>> positionKp = 0.3563;
        }
        part fcHeavyLift : FlightController {
            doc /* Tuned for heavyLift */
            attribute :>> rpmKp = 0.123;
            attribute :>> rpmKi = 2.394;
            attribute :>> rateKp = 31.98;
            attribute :>> rateKi = 144.4;
            attribute :>> rateKd = 0.2053;
            attribute :>> yawRateKp = 60.85;
            attribute :>> yawRateKi = 274.8;
            attribute :>> attitudeKp = 4.25;
            attribute :>> headingKp = 4.263;
            attribute :>> velocityKp = 1.417;
            attribute :>> velocityKi = 0.5018;
            attribute :>> verticalKp = 2.5;
            attribute :>> verticalKi = 1.562;
            attribute :>> positionKp = 0.3542;
        }
    }

    // ---------------------------------------------------------- behaviors
    // Abstract behaviors that architectures perform. The in parameters are the
    // performance of the drone that performs it; the attributes without a value
    // are quantities each mission step sets; duration and hoverTime are derived,
    // so a mission's energy is hoverPower * sum(hoverTime).
    // A specialized behavior's own parameters redefine the inherited ones by
    // position, so each restates powerFactor before adding its own.
    abstract #Behavior action def DroneBehavior {
        doc /* A mission step of a delivery drone. */
        in powerFactor : Real;                  // power draw, multiple of hover power
        attribute duration : Real;              // s
        attribute distance : Real default = 0;  // m flown
        attribute hoverTime : Real = duration * powerFactor;   // s of hover with the same energy
        attribute releasesPayload : Boolean default = false;   // the drone is lighter after this step
        // kinematics, for the dynamic simulation
        attribute groundSpeed : Real default = 0;     // m/s horizontal
        attribute verticalSpeed : Real default = 0;   // m/s, up is positive
        attribute airborne : Boolean default = true;  // false: on the ground, motors stopped
    }
    abstract #Behavior action def Takeoff :> DroneBehavior {
        doc /* Vertical climb to cruise altitude. */
        in :>> powerFactor;
        in climbRate : Real;                    // m/s
        attribute altitude : Real;              // m
        attribute :>> duration = altitude / climbRate;
        attribute :>> verticalSpeed = climbRate;
    }
    abstract #Behavior action def Cruise :> DroneBehavior {
        doc /* Level flight to the next waypoint. */
        in :>> powerFactor;
        in speed : Real;                        // m/s
        attribute :>> distance;
        attribute :>> duration = distance / speed;
        attribute :>> groundSpeed = speed;
    }
    abstract #Behavior action def Hover :> DroneBehavior {
        doc /* Hold position, e.g. waiting for a landing zone to clear. */
        in :>> powerFactor;
    }
    abstract #Behavior action def WinchLower :> DroneBehavior {
        doc /* Lower the parcel on the tether while hovering, release it and reel the tether in. */
        in :>> powerFactor;
        in winchSpeed : Real;                   // m/s
        attribute depth : Real;                 // m
        attribute :>> duration = 2 * depth / winchSpeed + 10;   // 10 s to release
        attribute :>> releasesPayload = true;
    }
    abstract #Behavior action def Land :> DroneBehavior {
        doc /* Vertical descent and touchdown. */
        in :>> powerFactor;
        in descentRate : Real;                  // m/s
        attribute altitude : Real;              // m
        attribute :>> duration = altitude / descentRate;
        attribute :>> verticalSpeed = -descentRate;
    }
    abstract #Behavior action def Turn :> DroneBehavior {
        doc /* Turn on the spot to a new heading. */
        in :>> powerFactor;
        in yawRate : Real;                      // deg/s
        attribute angle : Real;                 // deg
        attribute :>> duration = angle / yawRate;
    }
    abstract #Behavior action def GroundRelease :> DroneBehavior {
        doc /* Release the parcel on the ground with the motors idle. */
        in :>> powerFactor;
        attribute :>> releasesPayload = true;
        attribute :>> airborne = false;
    }

    // ---------------------------------------------------------- behavior catalog
    // Performance profiles: every parameter set, so a system instance can perform them.
    package Behaviors {
        action takeoffStandard : Takeoff { in :>> powerFactor = 1.4; in :>> climbRate = 3; }
        action takeoffGentle : Takeoff {
            doc /* Slow climb for heavy lifters */
            in :>> powerFactor = 1.3;
            in :>> climbRate = 2;
        }
        action cruise10 : Cruise { in :>> powerFactor = 1.05; in :>> speed = 10; }
        action cruise12 : Cruise { in :>> powerFactor = 1.1; in :>> speed = 12; }
        action cruise15 : Cruise { in :>> powerFactor = 1.2; in :>> speed = 15; }
        action cruiseSport : Cruise {
            doc /* Speed profile still being flight-tested: no power factor yet */
            in :>> speed = 18;
        }
        action holdPosition : Hover { in :>> powerFactor = 1.0; }
        action winch1 : WinchLower { in :>> powerFactor = 1.05; in :>> winchSpeed = 1; }
        action landStandard : Land { in :>> powerFactor = 0.8; in :>> descentRate = 2; }
        action landGentle : Land { in :>> powerFactor = 0.85; in :>> descentRate = 1.5; }
        action releaseIdle : GroundRelease { in :>> powerFactor = 0.05; }
        action turnStandard : Turn { in :>> powerFactor = 1.0; in :>> yawRate = 45; }
        action turnGentle : Turn {
            doc /* Slow turns for heavy lifters */
            in :>> powerFactor = 1.0;
            in :>> yawRate = 30;
        }
    }
}
