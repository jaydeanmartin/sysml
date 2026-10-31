package VehicleModel {
    private import ScalarValues::*;
    private import ISQ::*;
    private import AnalysisTooling::*;

    // ---------------------------------------------------------- requirements
    requirement def MassRequirement {
        doc /* The total vehicle mass shall not exceed the mass limit. */
        subject vehicle : Vehicle;
        attribute massLimit : MassValue = 2000 [SI::kg];
        require constraint { vehicle.mass <= massLimit }
    }

    requirement def RangeRequirement {
        doc /* The vehicle shall travel at least the required range on one tank. */
        subject vehicle : Vehicle;
        attribute requiredRange : Real = 500;   // km
        require constraint { vehicle.range >= requiredRange }
    }

    // ---------------------------------------------------------- interfaces
    port def FuelPort {
        out attribute fuelFlow : Real;
    }
    port def TorquePort {
        out attribute torque : Real;
    }

    // ---------------------------------------------------------- structure
    part def Engine {
        attribute mass : MassValue;
        attribute efficiency : Real;
        port fuelIn : ~FuelPort;
        port torqueOut : TorquePort;
    }
    part def FuelTank {
        attribute mass : MassValue;
        attribute capacity : Real;      // litres
        port fuelOut : FuelPort;
    }
    part def Transmission {
        attribute mass : MassValue;
        port torqueIn : ~TorquePort;
    }
    part def Vehicle {
        attribute mass : MassValue;
        attribute range : Real;
        part engine : Engine;
        part tank : FuelTank;
        part transmission : Transmission;
        connect tank.fuelOut to engine.fuelIn;
        connect engine.torqueOut to transmission.torqueIn;
    }

    // ---------------------------------------------------------- analyses (REST)
    // Performed by the analysis service: OpenSysML calls it during execution.
    calc def MassRollup {
        metadata ToolExecution { toolName = "massRollup"; uri = "analysis://massRollup"; }
        in engineMass : MassValue[1]       { @ToolVariable { name = "engineMass"; } }
        in tankMass : MassValue[1]         { @ToolVariable { name = "tankMass"; } }
        in transmissionMass : MassValue[1] { @ToolVariable { name = "transmissionMass"; } }
        return total : MassValue[1]        { @ToolVariable { name = "total"; } }
    }

    calc def RangeEstimate {
        metadata ToolExecution { toolName = "rangeEstimate"; uri = "analysis://rangeEstimate"; }
        in capacity : Real[1]   { @ToolVariable { name = "capacity"; } }
        in efficiency : Real[1] { @ToolVariable { name = "efficiency"; } }
        in mass : MassValue[1]  { @ToolVariable { name = "mass"; } }
        return range : Real[1]  { @ToolVariable { name = "range"; } }
    }

    // Performed by an *external* REST service registered in the analysis registry
    action def CoolingSolve {
        metadata ToolExecution { toolName = "thermalSolver"; uri = "analysis://thermalSolver"; }
        in power : PowerValue[1]         { @ToolVariable { name = "power"; } }
        in area : AreaValue[1]           { @ToolVariable { name = "area"; } }
        in ambient : TemperatureValue[1] { @ToolVariable { name = "ambient"; } }
        out tMax : TemperatureValue[1]   { @ToolVariable { name = "tMax"; } }
        out margin : TemperatureValue[1] { @ToolVariable { name = "margin"; } }
    }

    requirement def CoolingRequirement {
        doc /* The radiator shall keep coolant at or below 393 K (120 degC). */
        subject vehicle : Vehicle;
        in attribute tMax : TemperatureValue;
        require constraint { tMax <= 393.15 [SI::K] }
    }

    analysis def CoolingAnalysis {
        subject vehicle : Vehicle;
        in attribute heatLoad : PowerValue = 3000 [SI::W];
        in attribute radiatorArea : AreaValue = 1.5 [SI::'m²'];
        in attribute ambient : TemperatureValue = 308.15 [SI::K];
        action solve : CoolingSolve {
            in power = heatLoad;
            in area = radiatorArea;
            in ambient = ambient;
        }
        return tMax : TemperatureValue[1] = solve.tMax;
        out margin : TemperatureValue[1] = solve.margin;
        objective coolingHeld : CoolingRequirement {
            subject = vehicle;
            in tMax = solve.tMax;
        }
    }

    // A plain SysML calc, evaluated by OpenSysML itself
    calc def FuelEnergy {
        in litres : Real;
        return energy : Real = litres * 34.2;   // MJ
    }

    // ---------------------------------------------------------- behavior
    action def Drive {
        attribute distance : Real = 0;
        first start;
        then action ignite;
        then action accelerate { assign distance := distance + 2; }
        then action cruise { assign distance := distance + 120; }
        then action brake;
        then done;
    }

    state def VehicleStates {
        entry; then parked;
        state parked;
        state idling;
        state moving;
        transition first parked accept after 2 [SI::s] then idling;
        transition first idling accept after 3 [SI::s] then moving;
        transition first moving accept after 60 [SI::s] then parked;
    }

    // ---------------------------------------------------------- instances
    part sedan : Vehicle {
        part :>> engine { attribute :>> mass = 180 [SI::kg]; attribute :>> efficiency = 0.32; }
        part :>> tank { attribute :>> mass = 45 [SI::kg]; attribute :>> capacity = 55; }
        part :>> transmission { attribute :>> mass = 90 [SI::kg]; }
        attribute :>> mass = MassRollup(engine.mass, tank.mass, transmission.mass) + 1100 [SI::kg];
        attribute :>> range = RangeEstimate(tank.capacity, engine.efficiency, mass);
    }

    analysis sedanCooling : CoolingAnalysis { subject vehicle = sedan; }

    requirement sedanMass : MassRequirement { subject vehicle = sedan; }
    requirement sedanRange : RangeRequirement { subject vehicle = sedan; }
    satisfy sedanMass by sedan;
    satisfy sedanRange by sedan;
}
