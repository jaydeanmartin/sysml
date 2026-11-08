package DroneMissions {
    private import ScalarValues::*;
    private import DroneLibrary::*;
    private import DroneArchitectures::*;
    private import AnalysisTooling::*;

    // ---------------------------------------------------------- marker
    // The Missions tab configures missions of #Mission action defs.
    metadata def Mission {
        doc /* A mission type. Its missions are ordered lists of behaviors; each attribute is a total over the steps. */
    }

    // ---------------------------------------------------------- mission type
    #Mission action def DeliveryMission {
        doc /* Out-and-back parcel delivery. */
        // totals: each is the sum of the same attribute over the steps, as a drone flies them
        attribute duration : Real;
        attribute distance : Real;
        attribute hoverTime : Real;
    }

    // ---------------------------------------------------------- mission requirements
    // A requirement def with in parameters is a mission requirement, checked by
    // the Missions tab for every system instance and mission. Its parameters
    // are supplied by the mission simulation that verifies it (see below);
    // a requirement no simulation verifies takes the mission totals of the
    // same names instead, summed over the steps as the system flies them.
    requirement def MissionEnergyRequirement {
        doc /* The drone shall fly the mission on one charge, keeping a 20 % battery reserve. */
        subject drone : DeliveryDrone;
        in energyPercent : Real;                // of the battery's energy, used by the mission
        attribute reserve : Real = 0.2;
        require constraint { energyPercent <= 100 * (1 - reserve) }
    }
    requirement def MissionTimeRequirement {
        doc /* A delivery mission shall take at most 30 minutes. */
        subject drone : DeliveryDrone;
        in missionTime : Real;                  // s
        require constraint { missionTime <= 1800 }
    }
    requirement def MissionEnduranceEstimate {
        doc /* Design-time estimate: the mission's hover-equivalent time, at full takeoff mass, shall fit in the drone's hover endurance. */
        subject drone : DeliveryDrone;
        in hoverTime : Real;                    // a mission total
        require constraint { hoverTime <= drone.endurance * 60 }
    }

    // ---------------------------------------------------------- mission simulation
    // An external analysis (the mission simulator service, registered as
    // missionSimulator) flies a mission step by step: battery energy drawn,
    // percent of the pack, mission time, state of charge. It is a verification
    // case: its objective names the requirements it verifies, and its out
    // parameters the results they are checked with (by name).
    // The Missions tab calls it for every system instance it applies to: each
    // in parameter is evaluated with the system instance as subject, and the
    // flown steps are added as the input "steps".
    verification def MissionSimulation {
        doc /* Time-stepped simulation: power follows the mass (lighter after the parcel is released), with battery losses at high power. */
        metadata ToolExecution { toolName = "missionSimulator"; uri = "analysis://missionSimulator"; }
        subject drone : DeliveryDrone;
        in hoverPower : Real = drone.hoverPower;                            // W at takeoff mass
        in takeoffMass : Real = drone.takeoffMass / (1 [SI::kg]);
        in payloadMass : Real = drone.payloadCapacity / (1 [SI::kg]);       // dropped when a step releases it
        in batteryEnergy : Real = drone.battery.energy;                     // Wh
        in batteryMaxPower : Real = drone.battery.maxPower;                 // W
        in reserve : Real = 0.2;                                            // fraction kept back
        out energyPercent : Real;                                           // % of the battery's energy used
        out missionTime : Real;                                             // s
        out endStateOfCharge : Real;                                        // %
        objective {
            verify requirement energy : MissionEnergyRequirement;
            verify requirement time : MissionTimeRequirement;
        }
    }

    // ---------------------------------------------------------- dynamic simulation
    // A second external analysis (dynamicsSimulator) flies the mission through
    // the propulsion chain: the vehicle's acceleration transients and drag, the
    // propeller's thrust and power curves against the advance ratio, the
    // motor's torque, speed and efficiency, and the battery's voltage sag.
    // It reports the energy and power of every behavior, and verifies the
    // requirements that need those dynamics. Still air, constant air density.
    requirement def MissionPeakPowerRequirement {
        doc /* Across the mission, including acceleration transients, the electrical power shall stay within the battery's maximum power. */
        subject drone : DeliveryDrone;
        in peakPower : Real;                    // W
        require constraint { peakPower <= drone.battery.maxPower }
    }
    requirement def ThrottleHeadroomRequirement {
        doc /* The motors shall keep a 20 % throttle margin for control in every behavior of the mission. */
        subject drone : DeliveryDrone;
        in maxThrottle : Real;                  // motor voltage / bus voltage
        require constraint { maxThrottle <= 0.8 }
    }
    verification def DynamicSimulation {
        doc /* Propulsion dynamics: propeller CT, CP against advance ratio, brushless motor model, drag, acceleration transients and battery sag. */
        metadata ToolExecution { toolName = "dynamicsSimulator"; uri = "analysis://dynamicsSimulator"; }
        subject drone : DeliveryDrone;
        in rotorCount : Real = drone.rotorCount;
        in takeoffMass : Real = drone.takeoffMass / (1 [SI::kg]);
        in payloadMass : Real = drone.payloadCapacity / (1 [SI::kg]);
        in dragArea : Real = drone.airframe.dragArea;                       // m²
        in propDiameter : Real = drone.propellers.diameter#(1);             // in
        in ct0 : Real = drone.propellers.ct0#(1);
        in ct1 : Real = drone.propellers.ct1#(1);
        in ct2 : Real = drone.propellers.ct2#(1);
        in cp0 : Real = drone.propellers.cp0#(1);
        in cp1 : Real = drone.propellers.cp1#(1);
        in cp2 : Real = drone.propellers.cp2#(1);
        in motorKv : Real = drone.motors.kv#(1);                            // rpm/V
        in motorResistance : Real = drone.motors.resistance#(1);            // Ω
        in motorNoLoadCurrent : Real = drone.motors.noLoadCurrent#(1);      // A
        in motorMaxCurrent : Real = drone.motors.maxCurrent#(1);            // A
        in batteryVoltage : Real = drone.battery.voltage;                   // V
        in batteryEnergy : Real = drone.battery.energy;                     // Wh
        in batteryMaxPower : Real = drone.battery.maxPower;                 // W
        in airDensity : Real = 1.225;                                       // kg/m³, constant
        in reserve : Real = 0.2;
        out energyPercent : Real;                                           // % of the battery's energy used
        out peakPower : Real;                                               // W
        out maxThrottle : Real;
        objective {
            verify requirement peak : MissionPeakPowerRequirement;
            verify requirement headroom : ThrottleHeadroomRequirement;
        }
    }

    // ---------------------------------------------------------- missions
    // Created and edited from the Missions tab. Each step is an abstract
    // behavior with its quantities set; a drone flies it with the behavior it
    // performs for that type (its speed, climb rate, power factor...).
    package Missions {
        action shortHop : DeliveryMission {
            doc /* 2 km each way, land and drop off */
            first start;
            then action takeoff : Takeoff { attribute :>> altitude = 30; }
            then action cruise : Cruise { attribute :>> distance = 2000; }
            then action land : Land { attribute :>> altitude = 30; }
            then action groundRelease : GroundRelease { attribute :>> duration = 30; }
            then action turn : Turn { attribute :>> angle = 180; }
            then action takeoff_2 : Takeoff { attribute :>> altitude = 30; }
            then action cruise_2 : Cruise { attribute :>> distance = 2000; }
            then action land_2 : Land { attribute :>> altitude = 30; }
            then done;
        }
        action suburban : DeliveryMission {
            doc /* 5 km each way, wait for the landing zone, drop off */
            first start;
            then action takeoff : Takeoff { attribute :>> altitude = 60; }
            then action cruise : Cruise { attribute :>> distance = 5000; }
            then action hover : Hover { attribute :>> duration = 30; }
            then action land : Land { attribute :>> altitude = 60; }
            then action groundRelease : GroundRelease { attribute :>> duration = 30; }
            then action turn : Turn { attribute :>> angle = 180; }
            then action takeoff_2 : Takeoff { attribute :>> altitude = 60; }
            then action cruise_2 : Cruise { attribute :>> distance = 5000; }
            then action land_2 : Land { attribute :>> altitude = 60; }
            then done;
        }
        action winchDrop : DeliveryMission {
            doc /* 4 km each way, lower the parcel to a balcony without landing */
            first start;
            then action takeoff : Takeoff { attribute :>> altitude = 60; }
            then action cruise : Cruise { attribute :>> distance = 4000; }
            then action winchLower : WinchLower { attribute :>> depth = 20; }
            then action turn : Turn { attribute :>> angle = 180; }
            then action cruise_2 : Cruise { attribute :>> distance = 4000; }
            then action land : Land { attribute :>> altitude = 60; }
            then done;
        }
        action rural : DeliveryMission {
            doc /* 8 km each way to a farm, drop off */
            first start;
            then action takeoff : Takeoff { attribute :>> altitude = 60; }
            then action cruise : Cruise { attribute :>> distance = 8000; }
            then action land : Land { attribute :>> altitude = 60; }
            then action groundRelease : GroundRelease { attribute :>> duration = 30; }
            then action turn : Turn { attribute :>> angle = 180; }
            then action takeoff_2 : Takeoff { attribute :>> altitude = 60; }
            then action cruise_2 : Cruise { attribute :>> distance = 8000; }
            then action land_2 : Land { attribute :>> altitude = 60; }
            then done;
        }
    }
}
