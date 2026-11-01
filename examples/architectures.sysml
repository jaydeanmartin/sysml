package Architectures {
    private import ScalarValues::*;
    private import ISQ::*;
    private import ComponentLibrary::*;

    // ---------------------------------------------------------- requirements
    part def Vehicle {
        attribute mass : MassValue;
        attribute range : Real;             // km
    }

    requirement def MassRequirement {
        doc /* The vehicle mass shall not exceed 1800 kg. */
        subject vehicle : Vehicle;
        require constraint { vehicle.mass <= 1800 [SI::kg] }
    }
    requirement def RangeRequirement {
        doc /* The vehicle shall travel at least 450 km between refuelling or recharging. */
        subject vehicle : Vehicle;
        require constraint { vehicle.range >= 450 }
    }

    // ---------------------------------------------------------- system architectures
    // Each part usage typed by a component is a slot. A system instance fills
    // every slot with a catalog instance (Configurator tab).
    #SystemArchitecture part def CombustionVehicle :> Vehicle {
        doc /* Front-engine car with a fuel tank and a transmission. */
        part engine : Engine;
        part tank : FuelTank;
        part transmission : Transmission;
        connect tank.fuelOut to engine.fuelIn;
        connect engine.torqueOut to transmission.torqueIn;
        attribute :>> mass = engine.mass + tank.mass + transmission.mass + 1150 [SI::kg];
        attribute :>> range = tank.capacity * 34.2 * engine.efficiency * transmission.efficiency / 0.9;   // 0.9 MJ/km at the wheels
    }

    #SystemArchitecture part def ElectricVehicle :> Vehicle {
        doc /* Battery-electric car with one motor and a reducer. */
        part battery : Battery;
        part motor : ElectricMotor;
        part transmission : Transmission;
        connect battery.powerOut to motor.powerIn;
        connect motor.torqueOut to transmission.torqueIn;
        attribute :>> mass = battery.mass + motor.mass + transmission.mass + 1050 [SI::kg];
        attribute :>> range = battery.capacity * motor.efficiency * transmission.efficiency / 0.16;   // 0.16 kWh/km
    }

    // ---------------------------------------------------------- system instances
    // Created and edited from the Configurator tab.
    package Configurations {
        part economy : CombustionVehicle {
            doc /* Entry trim */
            part :>> engine :> ComponentLibrary::Catalog::i4Engine;
            part :>> tank :> ComponentLibrary::Catalog::tank45;
            part :>> transmission :> ComponentLibrary::Catalog::manual6;
        }
        part touring : CombustionVehicle {
            doc /* Long-distance trim */
            part :>> engine :> ComponentLibrary::Catalog::v6Engine;
            part :>> tank :> ComponentLibrary::Catalog::tank70;
            part :>> transmission :> ComponentLibrary::Catalog::auto8;
        }
        part ev60 : ElectricVehicle {
            part :>> battery :> ComponentLibrary::Catalog::pack60;
            part :>> motor :> ComponentLibrary::Catalog::motor150;
            part :>> transmission :> ComponentLibrary::Catalog::reducer;
        }
    }

    requirement economyMass : MassRequirement { subject vehicle = Configurations::economy; }
    requirement economyRange : RangeRequirement { subject vehicle = Configurations::economy; }
    requirement touringMass : MassRequirement { subject vehicle = Configurations::touring; }
    requirement touringRange : RangeRequirement { subject vehicle = Configurations::touring; }
    requirement ev60Mass : MassRequirement { subject vehicle = Configurations::ev60; }
    requirement ev60Range : RangeRequirement { subject vehicle = Configurations::ev60; }
    satisfy economyMass by Configurations::economy;
    satisfy economyRange by Configurations::economy;
    satisfy touringMass by Configurations::touring;
    satisfy touringRange by Configurations::touring;
    satisfy ev60Mass by Configurations::ev60;
    satisfy ev60Range by Configurations::ev60;
}
