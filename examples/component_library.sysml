package ComponentLibrary {
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
    port def FuelPort {
        out attribute fuelFlow : Real;      // l/h
    }
    port def TorquePort {
        out attribute torque : Real;        // N·m
    }
    port def PowerPort {
        out attribute power : PowerValue;
    }

    // ---------------------------------------------------------- component types
    #Component part def Engine {
        doc /* Internal combustion engine. */
        attribute mass : MassValue;
        attribute power : PowerValue;
        attribute efficiency : Real;
        port fuelIn : ~FuelPort;
        port torqueOut : TorquePort;
    }

    #Component part def ElectricMotor {
        doc /* Electric traction motor. */
        attribute mass : MassValue;
        attribute power : PowerValue;
        attribute efficiency : Real;
        port powerIn : ~PowerPort;
        port torqueOut : TorquePort;
    }

    #Component part def FuelTank {
        doc /* Fuel tank. */
        attribute mass : MassValue;
        attribute capacity : Real;          // litres
        port fuelOut : FuelPort;
    }

    #Component part def Battery {
        doc /* Traction battery pack. */
        attribute mass : MassValue;
        attribute capacity : Real;          // kWh
        port powerOut : PowerPort;
    }

    abstract #Component part def Transmission {
        doc /* Any transmission; a slot typed by it accepts every kind below. */
        attribute mass : MassValue;
        attribute efficiency : Real;
        port torqueIn : ~TorquePort;
    }
    #Component part def ManualTransmission :> Transmission {
        attribute gears : Integer;
    }
    #Component part def AutomaticTransmission :> Transmission {
        attribute gears : Integer;
    }
    #Component part def SingleSpeedReducer :> Transmission {
        attribute ratio : Real;
    }

    // ---------------------------------------------------------- catalog
    // Component instances: each one is a part usage that fixes the attribute
    // values of its component type. The Library tab creates and edits them.
    package Catalog {
        part i4Engine : Engine {
            doc /* 1.6 l four-cylinder */
            attribute :>> mass = 140 [SI::kg];
            attribute :>> power = 90000 [SI::W];
            attribute :>> efficiency = 0.36;
        }
        part v6Engine : Engine {
            doc /* 3.0 l V6 */
            attribute :>> mass = 210 [SI::kg];
            attribute :>> power = 220000 [SI::W];
            attribute :>> efficiency = 0.31;
        }
        part tank45 : FuelTank {
            attribute :>> mass = 38 [SI::kg];
            attribute :>> capacity = 45;
        }
        part tank70 : FuelTank {
            attribute :>> mass = 55 [SI::kg];
            attribute :>> capacity = 70;
        }
        part manual6 : ManualTransmission {
            attribute :>> mass = 60 [SI::kg];
            attribute :>> efficiency = 0.96;
            attribute :>> gears = 6;
        }
        part auto8 : AutomaticTransmission {
            attribute :>> mass = 85 [SI::kg];
            attribute :>> efficiency = 0.92;
            attribute :>> gears = 8;
        }
        part reducer : SingleSpeedReducer {
            attribute :>> mass = 25 [SI::kg];
            attribute :>> efficiency = 0.97;
            attribute :>> ratio = 9.7;
        }
        part motor150 : ElectricMotor {
            attribute :>> mass = 45 [SI::kg];
            attribute :>> power = 150000 [SI::W];
            attribute :>> efficiency = 0.93;
        }
        part pack60 : Battery {
            doc /* 60 kWh NMC pack */
            attribute :>> mass = 380 [SI::kg];
            attribute :>> capacity = 60;
        }
        part pack82 : Battery {
            doc /* 82 kWh NMC pack */
            attribute :>> mass = 480 [SI::kg];
            attribute :>> capacity = 82;
        }
    }
}
