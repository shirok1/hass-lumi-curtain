"""The supported subset of the HMCN02 MIoT service tree.

Keys are (service instance ID, property instance ID). Values stay in their
native integer representation until an HA entity converts them for display.
"""

MOTOR_CONTROL = (2, 1)
TARGET_POSITION = (2, 2)
STATUS = (2, 3)
POSITION = (2, 6)
AMBIENT_LIGHT = (3, 11)
BATTERY_TEMPERATURE = (3, 16)
BATTERY_LEVEL = (5, 1)
CHARGING_STATE = (5, 2)

CHARGING_STATES = {1: "charging", 2: "not_charging", 3: "not_chargeable"}
LIGHT_STATES = {0: "dark", 1: "bright", 2: "critical"}

STATE_PROPERTIES = (POSITION, STATUS)
SENSOR_PROPERTIES = {
    "battery_level": BATTERY_LEVEL,
    "charging_state": CHARGING_STATE,
    "battery_temperature": BATTERY_TEMPERATURE,
    "ambient_light": AMBIENT_LIGHT,
}
READ_PROPERTIES = STATE_PROPERTIES + tuple(SENSOR_PROPERTIES.values())
READ_RANGES = {
    POSITION: range(101),
    STATUS: range(3),
    BATTERY_LEVEL: range(101),
    CHARGING_STATE: range(1, 4),
    BATTERY_TEMPERATURE: range(-5000, 10001),
    AMBIENT_LIGHT: range(3),
}
WRITE_RANGES = {MOTOR_CONTROL: range(3), TARGET_POSITION: range(101)}
