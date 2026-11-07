// Interface for anything on the plane that adds fields to the report.
//
// HOW TO ADD A NEW SENSOR (e.g. an MPU6050):
//   1. Copy ExampleSensor.h to src/sensors/Mpu6050Sensor.h and rename the class.
//   2. Fill in the four methods:
//        name()      short id, used in serial logs
//        begin()     init hardware (Wire.begin, etc.); return false on failure
//                    -- a sensor whose begin() fails is skipped, the rest keep running
//        update()    called every loop(); read the hardware / do math here.
//                    Keep it fast (no long delay()), ranging runs in the same loop.
//        writeJson() called every report (200 ms); add your fields, e.g.
//                      out["ax"] = ax;
//                    Fields go into the report's "sensors" object, so
//                    prefix keys if they could clash with another sensor.
//   3. In main.cpp: #include it and add it to the SENSORS list.
//   4. Add any library to lib_deps in platformio.ini.
#pragma once

#include <ArduinoJson.h>

class Sensor {
 public:
  virtual ~Sensor() = default;
  virtual const char *name() const = 0;
  virtual bool begin() = 0;
  virtual void update() = 0;
  virtual void writeJson(JsonObject &out) = 0;
};
