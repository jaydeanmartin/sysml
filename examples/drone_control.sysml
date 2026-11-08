package DroneControl {
    private import ScalarValues::*;
    private import AnalysisTooling::*;
    private import DroneLibrary::*;
    private import DroneArchitectures::*;

    // ---------------------------------------------------------- control requirements
    // Checked with the results of the ControlSimulation that verifies them, for
    // every system instance (Configurator tab, Controllability): the margins and
    // authority below, and the control tests after them.
    requirement def ControlStabilityRequirement {
        doc /* Every control loop shall keep at least 45° of phase margin and 6 dB of gain margin. */
        subject drone : DeliveryDrone;
        in phaseMargin : Real;                  // deg, the smallest over the loops
        in gainMargin : Real;                   // dB
        require constraint { phaseMargin >= 45 and gainMargin >= 6 }
    }
    requirement def ControlAuthorityRequirement {
        doc /* The rotors shall lift 1.8 times the takeoff weight, reach the fastest commanded turn rate within half a second, and stay off their limits 95 % of the time in every control test. */
        subject drone : DeliveryDrone;
        in thrustMargin : Real;                 // maximum thrust / hover thrust
        in yawAuthority : Real;                 // yaw acceleration available / needed
        in saturation : Real;                   // % of a manoeuvre with a rotor at its limit
        require constraint { thrustMargin >= 1.8 and yawAuthority >= 1 and saturation <= 5 }
    }

    // ---------------------------------------------------------- control tests
    // Each test is a requirement the ControlSimulation verifies by flying it:
    // from a settled hover, a step of one command, held for `hold` s. Its
    // attributes say what to fly (`command`: climbRate, speed, heading or gust;
    // `step`, and for a turn its `rate`), its in parameters which results it is
    // checked with (riseTime, settlingTime, overshoot, maxTrackingError,
    // maxDeviation, finalError, saturation, maxTilt). Add a test by defining one
    // and verifying it in ControlSimulation's objective.
    metadata def ControlTest {
        doc /* A closed-loop manoeuvre the control simulation flies, specified and accepted by the requirement it tags. */
    }
    #ControlTest requirement def ClimbStepTest {
        doc /* From hover, the climb rate the drone takes off with shall settle within 4 s, with at most 20 % overshoot and no rotor at its limit more than 5 % of the time. */
        subject drone : DeliveryDrone;
        attribute command : String = "climbRate";
        attribute step : Real = drone.takeoff.climbRate;      // m/s
        attribute hold : Real = 4;                            // s
        in settlingTime : Real;
        in overshoot : Real;
        in saturation : Real;
        require constraint { settlingTime <= 4 and overshoot <= 20 and saturation <= 5 }
    }
    #ControlTest requirement def DescentStepTest {
        doc /* From hover, the descent rate the drone lands with shall settle within 4 s, with at most 20 % overshoot. */
        subject drone : DeliveryDrone;
        attribute command : String = "climbRate";
        attribute step : Real = -drone.land.descentRate;      // m/s
        attribute hold : Real = 4;
        in settlingTime : Real;
        in overshoot : Real;
        require constraint { settlingTime <= 4 and overshoot <= 20 }
    }
    #ControlTest requirement def SpeedStepTest {
        doc /* From hover, the cruise speed shall settle within 5 s with at most 15 % overshoot, tilting at most 35° and with no rotor at its limit more than 5 % of the time. */
        subject drone : DeliveryDrone;
        attribute command : String = "speed";
        attribute step : Real = drone.cruise.speed;           // m/s
        attribute hold : Real = 9;
        in settlingTime : Real;
        in overshoot : Real;
        in maxTilt : Real;
        in saturation : Real;
        require constraint { settlingTime <= 5 and overshoot <= 15 and maxTilt <= 35 and saturation <= 5 }
    }
    #ControlTest requirement def TurnTest {
        doc /* A 90° turn at the drone's turn rate shall lag the commanded heading by at most 10° and settle within 3 s of the end of the turn. */
        subject drone : DeliveryDrone;
        attribute command : String = "heading";
        attribute step : Real = 90;                           // deg
        attribute rate : Real = drone.turn.yawRate;           // deg/s
        attribute hold : Real = 4;
        in settlingTime : Real;
        in maxTrackingError : Real;
        require constraint { settlingTime <= 3 and maxTrackingError <= 10 }
    }
    #ControlTest requirement def GustTest {
        doc /* Holding position, the drone shall stay within 1 m when a 5 m/s gust hits it, and be back within 0.25 m after 7 s. */
        subject drone : DeliveryDrone;
        attribute command : String = "gust";
        attribute step : Real = 5;                            // m/s of wind
        attribute hold : Real = 7;
        in maxDeviation : Real;
        in finalError : Real;
        require constraint { maxDeviation <= 1 and finalError <= 0.25 }
    }

    // ---------------------------------------------------------- control simulations
    // External analysis controlSimulator (mission simulator service): a 6-DOF
    // rigid body with rotor and motor dynamics and reaction torques, flown by a
    // command mixer and cascaded PID loops down to each motor's ESC. Run for a
    // system instance on its own (#SystemSimulation), with the behaviors it
    // performs (each one's parameters are added as the input "behaviors").
    #SystemSimulation verification def ControlSimulation {
        doc /* Flies the control tests its objective verifies, closed-loop with the gains of the system's flight controller, and checks the stability margins and control authority. */
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
        out saturation : Real;                                              // % of the tests' time, the largest
        objective {
            verify requirement stability : ControlStabilityRequirement;
            verify requirement authority : ControlAuthorityRequirement;
            verify requirement climb : ClimbStepTest;
            verify requirement descent : DescentStepTest;
            verify requirement speed : SpeedStepTest;
            verify requirement turn : TurnTest;
            verify requirement gust : GustTest;
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
