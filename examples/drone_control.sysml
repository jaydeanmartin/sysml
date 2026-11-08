package DroneControl {
    private import ScalarValues::*;
    private import AnalysisTooling::*;
    private import DroneLibrary::*;
    private import DroneArchitectures::*;

    // ---------------------------------------------------------- control requirements
    // Checked with the results of the ControlSimulation that verifies them, for
    // every system instance (Configurator tab, Controllability).
    requirement def ControlStabilityRequirement {
        doc /* Every control loop shall keep at least 45° of phase margin and 6 dB of gain margin. */
        subject drone : DeliveryDrone;
        in phaseMargin : Real;                  // deg, the smallest over the loops
        in gainMargin : Real;                   // dB
        require constraint { phaseMargin >= 45 and gainMargin >= 6 }
    }
    requirement def ControlAuthorityRequirement {
        doc /* The rotors shall lift 1.8 times the takeoff weight, yaw at the commanded rate within half a second, and stay off their limits 95 % of the time in every manoeuvre. */
        subject drone : DeliveryDrone;
        in thrustMargin : Real;                 // maximum thrust / hover thrust
        in yawAuthority : Real;                 // yaw acceleration available / needed
        in saturation : Real;                   // % of a manoeuvre with a rotor at its limit
        require constraint { thrustMargin >= 1.8 and yawAuthority >= 1 and saturation <= 5 }
    }
    requirement def ManoeuvreResponseRequirement {
        doc /* A commanded speed shall settle within 5 s with at most 15 % overshoot; a turn within 6 s of its end. */
        subject drone : DeliveryDrone;
        in speedSettlingTime : Real;            // s
        in speedOvershoot : Real;               // %
        in headingSettlingTime : Real;          // s
        require constraint { speedSettlingTime <= 5 and speedOvershoot <= 15 and headingSettlingTime <= 6 }
    }
    requirement def GustRejectionRequirement {
        doc /* Holding position, the drone shall stay within 1 m when a 5 m/s gust hits it. */
        subject drone : DeliveryDrone;
        in gustDeviation : Real;                // m
        require constraint { gustDeviation <= 1 }
    }

    // ---------------------------------------------------------- control simulations
    // External analysis controlSimulator (mission simulator service): a 6-DOF
    // rigid body with rotor and motor dynamics and reaction torques, flown by a
    // command mixer and cascaded PID loops down to each motor's ESC. Run for a
    // system instance on its own (#SystemSimulation), with the behaviors it
    // performs (each one's parameters are added as the input "behaviors").
    #SystemSimulation verification def ControlSimulation {
        doc /* Closed-loop manoeuvres with the gains of the system's flight controller: a step of each performed behavior's command (climb rate, speed, a 90° turn at the yaw rate) and a hover hold in a gust. */
        metadata ToolExecution { toolName = "controlSimulator"; uri = "analysis://controlSimulator"; }
        subject drone : DeliveryDrone;
        in mode : String = "verify";
        in rotorCount : Real = drone.rotorCount;
        in takeoffMass : Real = drone.takeoffMass / (1 [SI::kg]);
        in armLength : Real = drone.armLength;                              // m
        in inertiaXX : Real = drone.inertiaXX;                              // kg·m²
        in inertiaYY : Real = drone.inertiaXX;
        in inertiaZZ : Real = drone.inertiaZZ;
        in rotorInertia : Real = drone.rotorInertia;
        in firstRotorSpin : Real = drone.firstRotorSpin;
        in dragArea : Real = drone.airframe.dragArea;                       // m²
        in propDiameter : Real = drone.propellers.diameter#(1);             // in
        in ct0 : Real = drone.propellers.ct0#(1);
        in cp0 : Real = drone.propellers.cp0#(1);
        in motorKv : Real = drone.motors.kv#(1);                            // rpm/V
        in motorResistance : Real = drone.motors.resistance#(1);            // Ω
        in motorNoLoadCurrent : Real = drone.motors.noLoadCurrent#(1);      // A
        in motorMaxCurrent : Real = drone.motors.maxCurrent#(1);            // A
        in batteryVoltage : Real = drone.battery.voltage;                   // V
        in gustSpeed : Real = 5;                                            // m/s
        // the flight controller's gains and limits
        in rpmKp : Real = drone.controller.rpmKp;
        in rpmKi : Real = drone.controller.rpmKi;
        in rateKp : Real = drone.controller.rateKp;
        in rateKi : Real = drone.controller.rateKi;
        in rateKd : Real = drone.controller.rateKd;
        in yawRateKp : Real = drone.controller.yawRateKp;
        in yawRateKi : Real = drone.controller.yawRateKi;
        in attitudeKp : Real = drone.controller.attitudeKp;
        in headingKp : Real = drone.controller.headingKp;
        in velocityKp : Real = drone.controller.velocityKp;
        in velocityKi : Real = drone.controller.velocityKi;
        in verticalKp : Real = drone.controller.verticalKp;
        in verticalKi : Real = drone.controller.verticalKi;
        in positionKp : Real = drone.controller.positionKp;
        in rpmLoop : Boolean = drone.controller.rpmLoop;
        in maxTilt : Real = drone.controller.maxTilt;                       // deg
        in maxYawRate : Real = drone.controller.maxYawRate;                 // deg/s
        out phaseMargin : Real;
        out gainMargin : Real;
        out thrustMargin : Real;
        out yawAuthority : Real;
        out saturation : Real;
        out speedSettlingTime : Real;
        out speedOvershoot : Real;
        out headingSettlingTime : Real;
        out gustDeviation : Real;
        objective {
            verify requirement stability : ControlStabilityRequirement;
            verify requirement authority : ControlAuthorityRequirement;
            verify requirement response : ManoeuvreResponseRequirement;
            verify requirement gust : GustRejectionRequirement;
        }
    }

    #SystemSimulation analysis def ControllerTuning {
        doc /* Open-loop tests (a voltage step on a held rotor, torque steps on a gimbal) identify the rotor and body dynamics; the gains follow from the identified models, inside out, with at least 45° of phase margin. Its out parameters are proposals for the attributes of the system's FlightController. */
        metadata ToolExecution { toolName = "controlSimulator"; uri = "analysis://controlSimulator"; }
        subject drone : DeliveryDrone;
        in mode : String = "tune";
        in rotorCount : Real = drone.rotorCount;
        in takeoffMass : Real = drone.takeoffMass / (1 [SI::kg]);
        in armLength : Real = drone.armLength;
        in inertiaXX : Real = drone.inertiaXX;
        in inertiaYY : Real = drone.inertiaXX;
        in inertiaZZ : Real = drone.inertiaZZ;
        in rotorInertia : Real = drone.rotorInertia;
        in firstRotorSpin : Real = drone.firstRotorSpin;
        in dragArea : Real = drone.airframe.dragArea;
        in propDiameter : Real = drone.propellers.diameter#(1);
        in ct0 : Real = drone.propellers.ct0#(1);
        in cp0 : Real = drone.propellers.cp0#(1);
        in motorKv : Real = drone.motors.kv#(1);
        in motorResistance : Real = drone.motors.resistance#(1);
        in motorNoLoadCurrent : Real = drone.motors.noLoadCurrent#(1);
        in motorMaxCurrent : Real = drone.motors.maxCurrent#(1);
        in batteryVoltage : Real = drone.battery.voltage;
        in gustSpeed : Real = 5;
        in rpmLoop : Boolean = true;
        in maxTilt : Real = 30;
        in maxYawRate : Real = 90;
        out rpmKp : Real;
        out rpmKi : Real;
        out rateKp : Real;
        out rateKi : Real;
        out rateKd : Real;
        out yawRateKp : Real;
        out yawRateKi : Real;
        out attitudeKp : Real;
        out headingKp : Real;
        out velocityKp : Real;
        out velocityKi : Real;
        out verticalKp : Real;
        out verticalKi : Real;
        out positionKp : Real;
    }
}
