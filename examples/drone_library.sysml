package DroneLibrary {
    private import ScalarValues::*;
    private import ISQ::*;
    private import AnalysisTooling::*;

    // ---------------------------------------------------------- markers
    // The Library and Configurator tabs look for these metadata tags.
    metadata def Component {
        doc /* A reusable component type. Its instances form the component catalog. */
    }
    metadata def SystemArchitecture {
        doc /* A system-level part def. Its part usages are slots that component instances fill, its performed actions behavior slots that behavior instances fill. */
    }
    metadata def Behavior {
        doc /* An abstract behavior: its in parameters are set by the system that performs it, its quantities by each mission step. */
    }

    // ---------------------------------------------------------- requirement library
    // Abstract requirements, reused by components, architectures, behaviors and
    // missions. A def includes one as a requirement usage and binds what it
    // constrains (`actual`); every instance then states its own number (the
    // minimum or maximum), so instances cannot leave a requirement open:
    //     requirement <'BAT-01'> specificEnergy : MinimumValue { attribute :>> actual = energy / (mass / (1 [SI::kg])); }
    //     part lipo4s5000 : Battery { ... requirement <'BAT-01'> specificEnergy :>> specificEnergy { attribute :>> minimum = 130; } }
    // Requirement ids are short names (<'BAT-01'>). A redefinition restates the
    // id: OpenSysML finds a feature that has a short name only by it.
    abstract requirement def <'LIB-01'> MinimumValue {
        doc /* The value shall be at least {minimum}. */
        attribute actual : Real;
        attribute minimum : Real;
        require constraint { actual >= minimum }
    }
    abstract requirement def <'LIB-02'> MaximumValue {
        doc /* The value shall not exceed {maximum}. */
        attribute actual : Real;
        attribute maximum : Real;
        require constraint { actual <= maximum }
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
    #Component part def Material {
        doc /* Structural material. Laminates: in-plane values, along the fibres for a tube. */
        attribute density : Real;           // kg/m³
        attribute youngsModulus : Real;     // GPa
        attribute shearModulus : Real;      // GPa
        attribute poissonRatio : Real;
        attribute strength : Real;          // MPa: yield, or tensile strength of a brittle laminate
        attribute fatigueStrength : Real;   // MPa, at 10⁷ cycles
    }

    #Component part def Frame {
        doc /* Airframe: a central hub and one arm per rotor. The wheelbase is the motor-to-motor diagonal. */
        attribute hubMass : MassValue;
        attribute armMass : MassValue;      // per arm
        attribute wheelbase : Real;         // mm
        attribute maxTakeoffMass : MassValue;
        attribute dragArea : Real;          // m², drag coefficient × frontal area (dynamic simulation)
        // structural geometry (structural analysis): round tube arms between two hub plates
        attribute armOuterDiameter : Real;  // mm
        attribute armWallThickness : Real;  // mm
        attribute hubDiameter : Real;       // mm, the plates' diameter; the arms run from its edge to the motors
        attribute hubPlateThickness : Real; // mm, each of the two plates
        part armMaterial : Material;
        part plateMaterial : Material;
        port payloadMount : MountPort;
    }

    #Component part def Battery {
        doc /* Flight battery pack. */
        attribute mass : MassValue;
        attribute voltage : Real;           // V (nominal)
        attribute energy : Real;            // Wh
        attribute maxPower : Real;          // W (continuous discharge)
        port powerOut : PowerPort;
        // each pack states the specific energy (Wh/kg) its datasheet guarantees
        requirement <'BAT-01'> specificEnergy : MinimumValue {
            doc /* The pack shall store at least {minimum} Wh per kg, harness included. */
            attribute :>> actual = energy / (mass / (1 [SI::kg]));
        }
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
        // torque coefficient CQ = CP / 2π, efficiency η = J CT / CP.
        // Estimated from the blade geometry by the PropellerAerodynamics analysis.
        attribute ct0 : Real;
        attribute ct1 : Real;
        attribute ct2 : Real;
        attribute cp0 : Real;
        attribute cp1 : Real;
        attribute cp2 : Real;
        // blade geometry (aerodynamic and structural analysis): stations from hub to tip
        attribute bladeCount : Integer;
        attribute stations : Real[*] ordered nonunique;       // r/R of each station
        attribute chord : Real[*] ordered nonunique;          // chord / tip radius at each station
        attribute twist : Real[*] ordered nonunique;          // deg, chord line to the rotation plane at each station
        attribute airfoil : String;         // section, as XFOIL names it: "NACA 4412"
        attribute hubRadius : Real;         // r/R where the blade starts
        part material : Material;
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

    // ---------------------------------------------------------- catalog
    // Component instances: each one is a part usage that fixes the attribute
    // values of its component type. The Library tab creates and edits them.
    package Catalog {
        // ------------------------------------------ materials (typical values)
        part nylonGF30 : Material {
            doc /* 30 % glass-filled nylon: injection-moulded propellers */
            attribute :>> density = 1360;
            attribute :>> youngsModulus = 7.5;
            attribute :>> shearModulus = 2.7;
            attribute :>> poissonRatio = 0.35;
            attribute :>> strength = 150;
            attribute :>> fatigueStrength = 45;
        }
        part cfrpWoven : Material {
            doc /* Woven carbon/epoxy laminate, quasi-isotropic: frame plates, moulded propellers */
            attribute :>> density = 1550;
            attribute :>> youngsModulus = 50;
            attribute :>> shearModulus = 19;
            attribute :>> poissonRatio = 0.3;
            attribute :>> strength = 550;
            attribute :>> fatigueStrength = 220;
        }
        part cfrpTube : Material {
            doc /* Roll-wrapped carbon tube, fibres mostly along the axis: frame arms */
            attribute :>> density = 1580;
            attribute :>> youngsModulus = 110;
            attribute :>> shearModulus = 5;
            attribute :>> poissonRatio = 0.3;
            attribute :>> strength = 1200;
            attribute :>> fatigueStrength = 450;
        }
        part al6061 : Material {
            doc /* 6061-T6 aluminium: motor mounts, folding-arm hinges */
            attribute :>> density = 2700;
            attribute :>> youngsModulus = 69;
            attribute :>> shearModulus = 26;
            attribute :>> poissonRatio = 0.33;
            attribute :>> strength = 276;
            attribute :>> fatigueStrength = 96;
        }

        // ------------------------------------------ structures (frame sizes)
        part frame450 : Frame {
            doc /* 450 mm carbon frame, small parcels */
            attribute :>> hubMass = 0.35 [SI::kg];
            attribute :>> armMass = 0.05 [SI::kg];
            attribute :>> wheelbase = 450;
            attribute :>> dragArea = 0.03;
            attribute :>> maxTakeoffMass = 5 [SI::kg];
            attribute :>> armOuterDiameter = 16;
            attribute :>> armWallThickness = 1;
            attribute :>> hubDiameter = 160;
            attribute :>> hubPlateThickness = 2;
            part :>> armMaterial :> cfrpTube;
            part :>> plateMaterial :> cfrpWoven;
        }
        part frame700 : Frame {
            doc /* 700 mm carbon frame */
            attribute :>> hubMass = 0.8 [SI::kg];
            attribute :>> armMass = 0.12 [SI::kg];
            attribute :>> wheelbase = 700;
            attribute :>> dragArea = 0.05;
            attribute :>> maxTakeoffMass = 12 [SI::kg];
            attribute :>> armOuterDiameter = 22;
            attribute :>> armWallThickness = 1.5;
            attribute :>> hubDiameter = 220;
            attribute :>> hubPlateThickness = 3;
            part :>> armMaterial :> cfrpTube;
            part :>> plateMaterial :> cfrpWoven;
        }
        part frame1200 : Frame {
            doc /* 1200 mm folding frame */
            attribute :>> hubMass = 1.7 [SI::kg];
            attribute :>> armMass = 0.26 [SI::kg];
            attribute :>> wheelbase = 1200;
            attribute :>> dragArea = 0.12;
            attribute :>> maxTakeoffMass = 22 [SI::kg];
            attribute :>> armOuterDiameter = 30;
            attribute :>> armWallThickness = 1.5;
            attribute :>> hubDiameter = 300;
            attribute :>> hubPlateThickness = 3;
            part :>> armMaterial :> cfrpTube;
            part :>> plateMaterial :> cfrpWoven;
        }
        part frame1600 : Frame {
            doc /* 1600 mm heavy-lift frame */
            attribute :>> hubMass = 2.8 [SI::kg];
            attribute :>> armMass = 0.4 [SI::kg];
            attribute :>> wheelbase = 1600;
            attribute :>> dragArea = 0.2;
            attribute :>> maxTakeoffMass = 35 [SI::kg];
            attribute :>> armOuterDiameter = 40;
            attribute :>> armWallThickness = 2;
            attribute :>> hubDiameter = 400;
            attribute :>> hubPlateThickness = 4;
            part :>> armMaterial :> cfrpTube;
            part :>> plateMaterial :> cfrpWoven;
        }

        // ------------------------------------------ batteries
        part lipo4s5000 : Battery {
            doc /* 4S 5000 mAh LiPo */
            attribute :>> mass = 0.55 [SI::kg];
            attribute :>> voltage = 14.8;
            attribute :>> energy = 74;
            attribute :>> maxPower = 2200;
            requirement <'BAT-01'> specificEnergy :>> specificEnergy { attribute :>> minimum = 130; }
        }
        part lipo4s8000 : Battery {
            doc /* 4S 8000 mAh LiPo */
            attribute :>> mass = 0.8 [SI::kg];
            attribute :>> voltage = 14.8;
            attribute :>> energy = 118;
            attribute :>> maxPower = 2400;
            requirement <'BAT-01'> specificEnergy :>> specificEnergy { attribute :>> minimum = 140; }
        }
        part lipo6s10000 : Battery {
            doc /* 6S 10000 mAh LiPo */
            attribute :>> mass = 1.35 [SI::kg];
            attribute :>> voltage = 22.2;
            attribute :>> energy = 222;
            attribute :>> maxPower = 5000;
            requirement <'BAT-01'> specificEnergy :>> specificEnergy { attribute :>> minimum = 160; }
        }
        part lipo6s22000 : Battery {
            doc /* 6S 22000 mAh LiPo */
            attribute :>> mass = 2.7 [SI::kg];
            attribute :>> voltage = 22.2;
            attribute :>> energy = 488;
            attribute :>> maxPower = 10000;
            requirement <'BAT-01'> specificEnergy :>> specificEnergy { attribute :>> minimum = 175; }
        }
        part liion6s30000 : Battery {
            doc /* 6S 30000 mAh Li-ion: more energy per kg, much less power */
            attribute :>> mass = 2.9 [SI::kg];
            attribute :>> voltage = 21.6;
            attribute :>> energy = 666;
            attribute :>> maxPower = 3000;
            requirement <'BAT-01'> specificEnergy :>> specificEnergy { attribute :>> minimum = 220; }
        }
        part lipo12s22000 : Battery {
            doc /* Two 6S 22000 mAh LiPo packs in series */
            attribute :>> mass = 5.6 [SI::kg];
            attribute :>> voltage = 44.4;
            attribute :>> energy = 977;
            attribute :>> maxPower = 20000;
            requirement <'BAT-01'> specificEnergy :>> specificEnergy { attribute :>> minimum = 180; }
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
            attribute :>> bladeCount = 2;
            attribute :>> stations = (0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95, 1);
            attribute :>> chord = (0.12, 0.15, 0.16, 0.155, 0.145, 0.13, 0.115, 0.095, 0.07, 0.05);
            attribute :>> twist = (43.7, 29.8, 22.3, 17.7, 14.6, 12.4, 10.8, 9.6, 8.6, 8.2);   // constant 4.5 in geometric pitch
            attribute :>> airfoil = "NACA 4412";
            attribute :>> hubRadius = 0.12;
            part :>> material :> nylonGF30;
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
            attribute :>> bladeCount = 2;
            attribute :>> stations = (0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95, 1);
            attribute :>> chord = (0.12, 0.15, 0.16, 0.155, 0.145, 0.13, 0.115, 0.095, 0.07, 0.05);
            attribute :>> twist = (35.3, 23, 16.9, 13.3, 10.9, 9.3, 8.1, 7.1, 6.4, 6.1);   // constant 5 in geometric pitch
            attribute :>> airfoil = "NACA 4412";
            attribute :>> hubRadius = 0.12;
            part :>> material :> nylonGF30;
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
            attribute :>> bladeCount = 2;
            attribute :>> stations = (0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95, 1);
            attribute :>> chord = (0.1, 0.12, 0.125, 0.12, 0.11, 0.1, 0.09, 0.075, 0.06, 0.04);
            attribute :>> twist = (34, 22.1, 16.1, 12.7, 10.4, 8.9, 7.7, 6.8, 6.1, 5.8);   // constant 7 in geometric pitch
            attribute :>> airfoil = "NACA 4410";
            attribute :>> hubRadius = 0.12;
            part :>> material :> cfrpWoven;
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
            attribute :>> bladeCount = 2;
            attribute :>> stations = (0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95, 1);
            attribute :>> chord = (0.1, 0.12, 0.125, 0.12, 0.11, 0.1, 0.09, 0.075, 0.06, 0.04);
            attribute :>> twist = (34.3, 22.3, 16.3, 12.8, 10.5, 8.9, 7.8, 6.9, 6.1, 5.8);   // constant 9 in geometric pitch
            attribute :>> airfoil = "NACA 4410";
            attribute :>> hubRadius = 0.12;
            part :>> material :> cfrpWoven;
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
        // each cruise profile states the speed it is cleared to fly at
        requirement <'CRZ-01'> speedLimit : MaximumValue {
            doc /* The cruise speed shall not exceed the cleared {maximum} m/s. */
            attribute :>> actual = speed;
        }
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
    abstract #Behavior action def GroundRelease :> DroneBehavior {
        doc /* Release the parcel on the ground with the motors idle. */
        in :>> powerFactor;
        attribute :>> releasesPayload = true;
        attribute :>> airborne = false;
    }

    // ---------------------------------------------------------- component analyses
    // An analysis bound to an external tool, with a component as its subject:
    // the Library runs it for a catalog instance. Its in parameters are
    // evaluated with the instance as subject; out parameters named like an
    // attribute of the component (ct0 … cp2, thrustPerWatt) can be written back.
    analysis def PropellerAerodynamics {
        doc /* XFOIL polars of the blade sections at their Reynolds and Mach numbers, and a blade-element momentum model with tip and hub losses: thrust and power coefficients against advance ratio, fitted as the CT, CP quadratics the dynamic simulation uses. */
        metadata ToolExecution { toolName = "propellerAero"; uri = "analysis://propellerAero"; }
        subject prop : Propeller;
        in diameter : Real = prop.diameter;             // in
        in bladeCount : Real = prop.bladeCount;
        in stations : Real[*] ordered nonunique = prop.stations;
        in chord : Real[*] ordered nonunique = prop.chord;
        in twist : Real[*] ordered nonunique = prop.twist;
        in airfoil : String = prop.airfoil;
        in hubRadius : Real = prop.hubRadius;
        in tipSpeed : Real = 110;                       // m/s: the speed the section polars are computed at (hover)
        in airDensity : Real = 1.225;                   // kg/m³
        in ncrit : Real = 9;                            // XFOIL transition amplification: 9 for quiet air
        out ct0 : Real;
        out ct1 : Real;
        out ct2 : Real;
        out cp0 : Real;
        out cp1 : Real;
        out cp2 : Real;
        out thrustPerWatt : Real;                       // N/W, static, at the reference speed
        out figureOfMerit : Real;
    }

    // ---------------------------------------------------------- tests
    // A verification def is a test: its objective names the requirements it
    // verifies, here a requirement usage reached through the subject.
    verification def BatteryAcceptanceTest {
        doc /* Incoming inspection: each pack is weighed and its capacity measured on a discharge bench. */
        subject battery : Battery;
        objective { verify battery.specificEnergy; }
    }
    verification def CruiseClearanceReview {
        doc /* Flight-envelope review of each cruise profile against the speed it is cleared for. */
        subject cruise : Cruise;
        objective { verify cruise.speedLimit; }
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
        action cruise10 : Cruise {
            in :>> powerFactor = 1.05;
            in :>> speed = 10;
            requirement <'CRZ-01'> speedLimit :>> speedLimit { attribute :>> maximum = 12; }
        }
        action cruise12 : Cruise {
            in :>> powerFactor = 1.1;
            in :>> speed = 12;
            requirement <'CRZ-01'> speedLimit :>> speedLimit { attribute :>> maximum = 14; }
        }
        action cruise15 : Cruise {
            in :>> powerFactor = 1.2;
            in :>> speed = 15;
            requirement <'CRZ-01'> speedLimit :>> speedLimit { attribute :>> maximum = 15; }
        }
        action cruiseSport : Cruise {
            doc /* Speed profile still being flight-tested: no power factor yet */
            in :>> speed = 18;
            requirement <'CRZ-01'> speedLimit :>> speedLimit { attribute :>> maximum = 16; }
        }
        action holdPosition : Hover { in :>> powerFactor = 1.0; }
        action winch1 : WinchLower { in :>> powerFactor = 1.05; in :>> winchSpeed = 1; }
        action landStandard : Land { in :>> powerFactor = 0.8; in :>> descentRate = 2; }
        action landGentle : Land { in :>> powerFactor = 0.85; in :>> descentRate = 1.5; }
        action releaseIdle : GroundRelease { in :>> powerFactor = 0.05; }
    }
}
