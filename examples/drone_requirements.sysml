package DroneRequirements {
    private import ScalarValues::*;
    private import ISQ::*;
    private import DroneLibrary::*;
    private import DroneArchitectures::*;

    // ---------------------------------------------------------- requirement types
    // Abstract and parameterised: each applies to an abstract system or component
    // type (its subject). Attributes are parameters, the targets and limits an
    // instance sets; in parameters (none here) are measured values supplied by
    // verification.
    package Types {
        // systems
        abstract requirement def ThrustToWeight {
            doc /* Full-throttle thrust shall be at least minRatio times the weight at maximum takeoff mass. */
            subject drone : DeliveryDrone;
            attribute minRatio : Real;
            require constraint { drone.thrustToWeight >= minRatio }
        }
        abstract requirement def HoverEndurance {
            doc /* The drone shall hover for at least minMinutes with a full payload, keeping a 20 % battery reserve. */
            subject drone : DeliveryDrone;
            attribute minMinutes : Real;
            require constraint { drone.endurance >= minMinutes }
        }
        abstract requirement def FrameLoad {
            doc /* The takeoff mass shall be at most maxLoadFraction of the frame's rated maximum takeoff mass. */
            subject drone : DeliveryDrone;
            attribute maxLoadFraction : Real;
            require constraint { drone.takeoffMass <= maxLoadFraction * drone.airframe.maxTakeoffMass }
        }
        abstract requirement def PropellerClearance {
            doc /* Adjacent propeller tips shall be at least minClearance mm apart. */
            subject drone : DeliveryDrone;
            attribute minClearance : Real;                  // mm
            require constraint { drone.propClearance >= minClearance }
        }
        abstract requirement def PropellerFit {
            doc /* The propeller shall be at least margin inches smaller than the motor's largest rated propeller. */
            subject drone : DeliveryDrone;
            attribute margin : Real;                        // in
            require constraint { drone.propellers.diameter#(1) + margin <= drone.motors.maxPropDiameter#(1) }
        }
        abstract requirement def BatteryPowerMargin {
            doc /* The battery shall supply every motor at full throttle with a margin. */
            subject drone : DeliveryDrone;
            attribute margin : Real;                        // fraction of the peak power
            require constraint { drone.peakPower * (1 + margin) <= drone.battery.maxPower }
        }
        // components
        abstract requirement def MotorEfficiency {
            doc /* The motor and its controller shall convert at least minimum of the electrical power to shaft power. */
            subject motor : Motor;
            attribute minimum : Real;
            require constraint { motor.efficiency >= minimum }
        }
        abstract requirement def BatteryDischarge {
            doc /* The battery shall deliver its maximum power at a discharge rate of at least minRate C. */
            subject battery : Battery;
            attribute minRate : Real;                       // C: maximum power / energy, per hour
            require constraint { battery.maxPower / battery.energy >= minRate }
        }
        abstract requirement def PayloadModuleMass {
            doc /* A payload module shall weigh at most maxFraction of the heaviest parcel it carries. */
            subject module : PayloadModule;
            attribute maxFraction : Real;
            require constraint { module.mass <= maxFraction * module.capacity }
        }
    }

    // ---------------------------------------------------------- specification
    // The requirement hierarchy: statements (a doc and an id, no constraint) hold
    // more specific statements or requirement instances (a type with its
    // parameters set). A statement holds for a system or component when every
    // requirement under it that applies does.
    //
    // Applicability: an instance applies to what its satisfy relations name; with
    // none, to every system or component instance its subject type fits.
    package Specification {
        requirement <'STK-1'> deliverParcels {
            doc /* The drone shall deliver parcels of up to 10 kg safely and reliably. */

            requirement <'SYS-1'> flightPerformance {
                doc /* The drone shall have the lift and endurance a delivery flight needs. */
                requirement <'SYS-1.1'> liftMargin : Types::ThrustToWeight {
                    doc /* Full-throttle thrust shall be at least twice the weight at maximum takeoff mass. */
                    attribute :>> minRatio = 2.0;
                }
                requirement <'SYS-1.2'> hoverEndurance : Types::HoverEndurance {
                    doc /* The drone shall hover for at least 15 minutes with a full payload. */
                    attribute :>> minMinutes = 15;
                }
                requirement <'SYS-1.3'> heavyLiftMargin : Types::ThrustToWeight {
                    doc /* A heavy lifter shall have 2.5 times its weight in thrust, for winch work in wind. */
                    attribute :>> minRatio = 2.5;
                }
            }
            requirement <'SYS-2'> structuralIntegrity {
                doc /* The airframe and rotors shall stay within their ratings. */
                requirement <'SYS-2.1'> frameLoad : Types::FrameLoad {
                    doc /* The takeoff mass shall not exceed the frame's rated maximum takeoff mass. */
                    attribute :>> maxLoadFraction = 1.0;
                }
                requirement <'SYS-2.2'> propClearance : Types::PropellerClearance {
                    doc /* Adjacent propeller tips shall not overlap. */
                    attribute :>> minClearance = 0;
                }
                requirement <'SYS-2.3'> propFit : Types::PropellerFit {
                    doc /* The propeller shall be no larger than the motor's largest rated propeller. */
                    attribute :>> margin = 0;
                }
            }
            requirement <'SYS-3'> powerSupply {
                doc /* The battery shall power the drone through every manoeuvre. */
                requirement <'SYS-3.1'> batteryPower : Types::BatteryPowerMargin {
                    doc /* The battery shall supply every motor at full throttle. */
                    attribute :>> margin = 0;
                }
            }
            requirement <'CMP-1'> componentQuality {
                doc /* Components shall meet the procurement specification. */
                requirement <'CMP-1.1'> motorEfficiency : Types::MotorEfficiency {
                    doc /* Motors shall be at least 82 % efficient with their controller. */
                    attribute :>> minimum = 0.82;
                }
                requirement <'CMP-1.2'> batteryDischarge : Types::BatteryDischarge {
                    doc /* Batteries shall deliver their power at 10 C or more. */
                    attribute :>> minRate = 10;
                }
                requirement <'CMP-1.3'> payloadModuleMass : Types::PayloadModuleMass {
                    doc /* Payload modules shall weigh at most 20 % of their capacity. */
                    attribute :>> maxFraction = 0.2;
                }
            }
        }

        // only the heavy lifter must meet the stricter lift margin
        satisfy deliverParcels.flightPerformance.heavyLiftMargin by DroneArchitectures::Configurations::heavyLift;
    }
}
