package DroneLibrary {
    private import ScalarValues::*;
    private import ISQ::*;

    // ---------------------------------------------------------- markers
    // The Library and Configurator tabs look for these two metadata tags.
    metadata def Component {
        doc /* A reusable component type. Its instances form the component catalog. */
    }
    metadata def SystemArchitecture {
        doc /* A system-level part def. Its part usages are slots that component instances fill. */
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
        port powerIn : ~PowerPort;
        port shaft : ShaftPort;
    }

    #Component part def Propeller {
        doc /* Fixed-pitch propeller. */
        attribute mass : MassValue;
        attribute diameter : Real;          // in
        attribute pitch : Real;             // in
        attribute thrustPerWatt : Real;     // N per W of shaft power, at hover
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
        // ------------------------------------------ structures (frame sizes)
        part frame450 : Frame {
            doc /* 450 mm carbon frame, small parcels */
            attribute :>> hubMass = 0.35 [SI::kg];
            attribute :>> armMass = 0.05 [SI::kg];
            attribute :>> wheelbase = 450;
            attribute :>> maxTakeoffMass = 5 [SI::kg];
        }
        part frame700 : Frame {
            doc /* 700 mm carbon frame */
            attribute :>> hubMass = 0.8 [SI::kg];
            attribute :>> armMass = 0.12 [SI::kg];
            attribute :>> wheelbase = 700;
            attribute :>> maxTakeoffMass = 12 [SI::kg];
        }
        part frame1200 : Frame {
            doc /* 1200 mm folding frame */
            attribute :>> hubMass = 1.7 [SI::kg];
            attribute :>> armMass = 0.26 [SI::kg];
            attribute :>> wheelbase = 1200;
            attribute :>> maxTakeoffMass = 22 [SI::kg];
        }
        part frame1600 : Frame {
            doc /* 1600 mm heavy-lift frame */
            attribute :>> hubMass = 2.8 [SI::kg];
            attribute :>> armMass = 0.4 [SI::kg];
            attribute :>> wheelbase = 1600;
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
        }
        part m4114 : Motor {
            doc /* 4114 400 KV */
            attribute :>> mass = 0.17 [SI::kg];
            attribute :>> maxPower = 700;
            attribute :>> efficiency = 0.83;
            attribute :>> maxPropDiameter = 15;
        }
        part m6215 : Motor {
            doc /* 6215 180 KV */
            attribute :>> mass = 0.42 [SI::kg];
            attribute :>> maxPower = 1500;
            attribute :>> efficiency = 0.85;
            attribute :>> maxPropDiameter = 22;
        }
        part m8318 : Motor {
            doc /* 8318 100 KV */
            attribute :>> mass = 0.68 [SI::kg];
            attribute :>> maxPower = 3000;
            attribute :>> efficiency = 0.86;
            attribute :>> maxPropDiameter = 30;
        }

        // ------------------------------------------ propellers
        part prop10x4_5 : Propeller {
            attribute :>> mass = 0.018 [SI::kg];
            attribute :>> diameter = 10;
            attribute :>> pitch = 4.5;
            attribute :>> thrustPerWatt = 0.085;
        }
        part prop15x5 : Propeller {
            attribute :>> mass = 0.035 [SI::kg];
            attribute :>> diameter = 15;
            attribute :>> pitch = 5;
            attribute :>> thrustPerWatt = 0.11;
        }
        part prop22x7 : Propeller {
            doc /* Carbon fibre */
            attribute :>> mass = 0.085 [SI::kg];
            attribute :>> diameter = 22;
            attribute :>> pitch = 7;
            attribute :>> thrustPerWatt = 0.13;
        }
        part prop28x9 : Propeller {
            doc /* Carbon fibre */
            attribute :>> mass = 0.14 [SI::kg];
            attribute :>> diameter = 28;
            attribute :>> pitch = 9;
            attribute :>> thrustPerWatt = 0.15;
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
}
