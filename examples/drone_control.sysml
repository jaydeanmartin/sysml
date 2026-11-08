package DroneControl {
    private import ScalarValues::*;
    private import AnalysisTooling::*;
    private import DroneLibrary::*;
    private import DroneArchitectures::*;
    private import DroneRequirements::Types::*;

    // ---------------------------------------------------------- control requirements
    // Their types are in DroneRequirements::Types (ControlStability,
    // ControlAuthority and the #ControlTest types), their instances, with the
    // limits each system must meet, in the specification (SYS-4). The
    // ControlSimulation below verifies the types: for a system instance it flies
    // every control test instance that applies to the system and checks every
    // instance with its results (Configurator tab, Controllability).

    // ---------------------------------------------------------- control simulations
    // External analysis controlSimulator (mission simulator service): a 6-DOF
    // rigid body with rotor and motor dynamics and reaction torques, flown by a
    // command mixer and cascaded PID loops down to each motor's ESC. Run for a
    // system instance on its own (#SystemSimulation), with the behaviors it
    // performs (each one's parameters are added as the input "behaviors").
    #SystemSimulation verification def ControlSimulation {
        doc /* Flies the control test instances that apply to the system (each a type its objective verifies), closed-loop with the gains of the system's flight controller, and checks the stability margins and control authority. */
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
            verify requirement stability : ControlStability;
            verify requirement authority : ControlAuthority;
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
