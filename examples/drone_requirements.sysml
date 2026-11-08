package DroneRequirements {
    private import ScalarValues::*;
    private import ISQ::*;
    private import DroneLibrary::*;
    private import DroneArchitectures::*;

    // ---------------------------------------------------------- requirement types
    // Abstract and parameterised: each applies to an abstract system or component
    // type (its subject). Attributes are parameters, the targets and limits an
    // instance sets; in parameters are measured values supplied by
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
        // missions: checked with measured values (in parameters) that a mission
        // simulation or the mission totals supply, for a drone flying the mission
        abstract requirement def MissionEnergy {
            doc /* The drone shall fly the mission on one charge, keeping a battery reserve. */
            subject drone : DeliveryDrone;
            attribute reserve : Real;                       // fraction of the battery kept back
            in energyPercent : Real;                        // % of the battery's energy the mission uses
            require constraint { energyPercent <= 100 * (1 - reserve) }
        }
        abstract requirement def MissionTime {
            doc /* The mission shall take at most limit seconds. */
            subject drone : DeliveryDrone;
            attribute limit : Real;                         // s
            in missionTime : Real;                          // s
            require constraint { missionTime <= limit }
        }
        abstract requirement def MissionEnduranceEstimate {
            doc /* Design-time estimate: the mission's hover-equivalent time, at full takeoff mass, shall fit in maxFraction of the drone's hover endurance. */
            subject drone : DeliveryDrone;
            attribute maxFraction : Real;
            in hoverTime : Real;                            // a mission total, s
            require constraint { hoverTime <= maxFraction * drone.endurance * 60 }
        }
        abstract requirement def MissionPeakPower {
            doc /* Across the mission, including acceleration transients, the electrical power shall stay within the battery's maximum power, with a margin. */
            subject drone : DeliveryDrone;
            attribute margin : Real;
            in peakPower : Real;                            // W
            require constraint { peakPower * (1 + margin) <= drone.battery.maxPower }
        }
        abstract requirement def ThrottleHeadroom {
            doc /* The motors shall keep a throttle margin for control in every behavior of the mission. */
            subject drone : DeliveryDrone;
            attribute limit : Real;                         // motor voltage / bus voltage
            in maxThrottle : Real;
            require constraint { maxThrottle <= limit }
        }
        abstract requirement def PayloadModuleMass {
            doc /* A payload module shall weigh at most maxFraction of the heaviest parcel it carries. */
            subject module : PayloadModule;
            attribute maxFraction : Real;
            require constraint { module.mass <= maxFraction * module.capacity }
        }
        // control: checked with the results of DroneControl::ControlSimulation
        abstract requirement def ControlStability {
            doc /* Every control loop shall keep at least minPhaseMargin of phase margin and minGainMargin of gain margin. */
            subject drone : DeliveryDrone;
            attribute minPhaseMargin : Real;                // deg
            attribute minGainMargin : Real;                 // dB
            in phaseMargin : Real;                          // deg, the smallest over the loops
            in gainMargin : Real;                           // dB
            require constraint { phaseMargin >= minPhaseMargin and gainMargin >= minGainMargin }
        }
        abstract requirement def ControlAuthority {
            doc /* The rotors shall lift minThrustMargin times the takeoff weight, reach the fastest commanded turn rate within half a second, and stay off their limits for all but maxSaturation % of every control test. */
            subject drone : DeliveryDrone;
            attribute minThrustMargin : Real;
            attribute minYawAuthority : Real;
            attribute maxSaturation : Real;                 // %
            in thrustMargin : Real;                         // maximum thrust / hover thrust
            in yawAuthority : Real;                         // yaw acceleration available / needed
            in saturation : Real;                           // % of a manoeuvre with a rotor at its limit
            require constraint { thrustMargin >= minThrustMargin and yawAuthority >= minYawAuthority and saturation <= maxSaturation }
        }
        // control tests: closed-loop manoeuvres the control simulation flies, from a
        // settled hover, a step of one command held for `hold` s. The attributes a
        // type gives a value say what to fly (`command`: climbRate, speed, heading
        // or gust; `step`, and for a turn its `rate`; an instance may override
        // them), those it leaves open are the limits each instance sets; the in
        // parameters are the results it is checked with (riseTime, settlingTime,
        // overshoot, maxTrackingError, maxDeviation, finalError, saturation, maxTilt).
        metadata def ControlTest {
            doc /* A closed-loop manoeuvre the control simulation flies, specified and accepted by the requirement it tags. */
        }
        abstract #ControlTest requirement def ClimbStepTest {
            doc /* From hover, the climb rate the drone takes off with shall settle within settlingLimit s, overshoot by at most overshootLimit %, and keep every rotor off its limit for all but saturationLimit % of the time. */
            subject drone : DeliveryDrone;
            attribute command : String = "climbRate";
            attribute step : Real default = drone.takeoff.climbRate;   // m/s
            attribute hold : Real default = 4;                         // s
            attribute settlingLimit : Real;                            // s
            attribute overshootLimit : Real;                           // %
            attribute saturationLimit : Real;                          // %
            in settlingTime : Real;
            in overshoot : Real;
            in saturation : Real;
            require constraint { settlingTime <= settlingLimit and overshoot <= overshootLimit and saturation <= saturationLimit }
        }
        abstract #ControlTest requirement def DescentStepTest {
            doc /* From hover, the descent rate the drone lands with shall settle within settlingLimit s and overshoot by at most overshootLimit %. */
            subject drone : DeliveryDrone;
            attribute command : String = "climbRate";
            attribute step : Real default = -drone.land.descentRate;   // m/s
            attribute hold : Real default = 4;
            attribute settlingLimit : Real;
            attribute overshootLimit : Real;
            in settlingTime : Real;
            in overshoot : Real;
            require constraint { settlingTime <= settlingLimit and overshoot <= overshootLimit }
        }
        abstract #ControlTest requirement def SpeedStepTest {
            doc /* From hover, the cruise speed shall settle within settlingLimit s and overshoot by at most overshootLimit %, tilting at most tiltLimit° and keeping every rotor off its limit for all but saturationLimit % of the time. */
            subject drone : DeliveryDrone;
            attribute command : String = "speed";
            attribute step : Real default = drone.cruise.speed;        // m/s
            attribute hold : Real default = 9;
            attribute settlingLimit : Real;
            attribute overshootLimit : Real;
            attribute tiltLimit : Real;                                // deg
            attribute saturationLimit : Real;
            in settlingTime : Real;
            in overshoot : Real;
            in maxTilt : Real;
            in saturation : Real;
            require constraint { settlingTime <= settlingLimit and overshoot <= overshootLimit and maxTilt <= tiltLimit and saturation <= saturationLimit }
        }
        abstract #ControlTest requirement def TurnTest {
            doc /* A turn at the drone's turn rate shall lag the commanded heading by at most trackingLimit° and settle within settlingLimit s of the end of the turn. */
            subject drone : DeliveryDrone;
            attribute command : String = "heading";
            attribute step : Real default = 90;                        // deg
            attribute rate : Real default = drone.turn.yawRate;        // deg/s
            attribute hold : Real default = 4;
            attribute settlingLimit : Real;
            attribute trackingLimit : Real;                            // deg
            in settlingTime : Real;
            in maxTrackingError : Real;
            require constraint { settlingTime <= settlingLimit and maxTrackingError <= trackingLimit }
        }
        abstract #ControlTest requirement def GustTest {
            doc /* Holding position, the drone shall stay within deviationLimit m when a gust of step m/s hits it, and be back within finalErrorLimit m after hold s. */
            subject drone : DeliveryDrone;
            attribute command : String = "gust";
            attribute step : Real default = 5;                         // m/s of wind
            attribute hold : Real default = 7;
            attribute deviationLimit : Real;                           // m
            attribute finalErrorLimit : Real;                          // m
            in maxDeviation : Real;
            in finalError : Real;
            require constraint { maxDeviation <= deviationLimit and finalError <= finalErrorLimit }
        }
    }

    // ---------------------------------------------------------- specification
    // Mission requirements are not here: they are instances in the objectives of
    // the mission types and missions (DroneMissions), and apply to the drones a
    // mission is assigned to.
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
            requirement <'SYS-4'> controllability {
                doc /* The flight controller shall fly the drone's behaviors stably, with control authority to spare. */
                requirement <'SYS-4.1'> stability : Types::ControlStability {
                    doc /* Every control loop shall keep at least 45° of phase margin and 6 dB of gain margin. */
                    attribute :>> minPhaseMargin = 45;
                    attribute :>> minGainMargin = 6;
                }
                requirement <'SYS-4.2'> authority : Types::ControlAuthority {
                    doc /* The rotors shall lift 1.8 times the takeoff weight, reach the turn rate within half a second, and be at their limits at most 5 % of any control test. */
                    attribute :>> minThrustMargin = 1.8;
                    attribute :>> minYawAuthority = 1;
                    attribute :>> maxSaturation = 5;
                }
                requirement <'SYS-4.3'> climbResponse : Types::ClimbStepTest {
                    doc /* The takeoff climb rate shall settle within 4 s with at most 20 % overshoot. */
                    attribute :>> settlingLimit = 4;
                    attribute :>> overshootLimit = 20;
                    attribute :>> saturationLimit = 5;
                }
                requirement <'SYS-4.4'> descentResponse : Types::DescentStepTest {
                    doc /* The landing descent rate shall settle within 4 s with at most 20 % overshoot. */
                    attribute :>> settlingLimit = 4;
                    attribute :>> overshootLimit = 20;
                }
                requirement <'SYS-4.5'> speedResponse : Types::SpeedStepTest {
                    doc /* The cruise speed shall settle within 5 s with at most 15 % overshoot, tilting at most 35°. */
                    attribute :>> settlingLimit = 5;
                    attribute :>> overshootLimit = 15;
                    attribute :>> tiltLimit = 35;
                    attribute :>> saturationLimit = 5;
                }
                requirement <'SYS-4.6'> turnResponse : Types::TurnTest {
                    doc /* A 90° turn shall lag the heading by at most 10° and settle within 3 s. */
                    attribute :>> settlingLimit = 3;
                    attribute :>> trackingLimit = 10;
                }
                requirement <'SYS-4.7'> gustRejection : Types::GustTest {
                    doc /* A 5 m/s gust shall move the drone at most 1 m, and it shall be back within 0.25 m after 7 s. */
                    attribute :>> deviationLimit = 1;
                    attribute :>> finalErrorLimit = 0.25;
                }
                requirement <'SYS-4.8'> heavyLiftTurn : Types::TurnTest {
                    doc /* A heavy lifter, slow in yaw, shall turn 180° lagging at most 15° and settle within 4 s. */
                    attribute :>> step = 180;
                    attribute :>> settlingLimit = 4;
                    attribute :>> trackingLimit = 15;
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

        // per-system limits: an instance a satisfy relation names a system for
        // replaces, for that system, the instances of the same type that apply to
        // it by subject type (the heavy lifter needs more lift, and turns slower)
        satisfy deliverParcels.flightPerformance.heavyLiftMargin by DroneArchitectures::Configurations::heavyLift;
        satisfy deliverParcels.controllability.heavyLiftTurn by DroneArchitectures::Configurations::heavyLift;
    }
}
