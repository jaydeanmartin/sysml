package DroneMissions {
    private import ScalarValues::*;
    private import DroneLibrary::*;
    private import DroneArchitectures::*;

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
    // A requirement def with in parameters named like mission totals is checked
    // by the Missions tab for every system instance and mission.
    requirement def MissionEnergyRequirement {
        doc /* The drone shall fly the mission on one charge, keeping a 20 % battery reserve. */
        subject drone : DeliveryDrone;
        in hoverTime : Real;
        require constraint { drone.hoverPower * hoverTime / 3600 <= drone.battery.energy * 0.8 }
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
            then action takeoff_2 : Takeoff { attribute :>> altitude = 60; }
            then action cruise_2 : Cruise { attribute :>> distance = 8000; }
            then action land_2 : Land { attribute :>> altitude = 60; }
            then done;
        }
    }
}
