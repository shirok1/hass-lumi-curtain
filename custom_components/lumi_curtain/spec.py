"""The supported subset of the HMCN02 MIoT service tree.

Keys are (service instance ID, property instance ID). Values stay in their
native integer representation until an HA entity converts them for display.
"""

MOTOR_CONTROL = (2, 1)
TARGET_POSITION = (2, 2)
STATUS = (2, 3)
FAULT = (2, 4)
POSITION = (2, 6)
WAKE_UP = (2, 7)
MANUAL_START = (3, 4)
RUN_TIME = (3, 8)
HOLD_STATE = (3, 10)
AMBIENT_LIGHT = (3, 11)
BATTERY_TEMPERATURE = (3, 16)
BATTERY_LEVEL = (5, 1)
CHARGING_STATE = (5, 2)
IDENTIFY_ACTION = (4, 1)
DEVICE_PROPERTIES = ((1, 1), (1, 2), (1, 3), (1, 4))

CHARGING_STATES = {1: "charging", 2: "not_charging", 3: "not_chargeable"}
LIGHT_STATES = {0: "dark", 1: "bright", 2: "critical"}
HOLD_STATES = {0: "unlocked", 1: "locked", 2: "locking", 3: "unlocking"}
BUTTON_KEYS = ("wake_up", "identify")
SWITCH_PROPERTIES = {"manual_start": MANUAL_START}
EVENT_TYPES = {
    "manual_operation": {(2, 1): "opened", (2, 2): "closed"},
    "battery_alert": {
        (5, 1): "low_battery",
        (3, 4): "critical_battery",
        (3, 5): "low_battery_automation",
        (3, 7): "discharge_temperature_normal",
        (3, 8): "discharge_overtemperature",
        (3, 9): "discharge_undertemperature",
        (3, 10): "charge_overtemperature",
        (3, 11): "charge_undertemperature",
    },
    "device_event": {(3, 3): "factory_reset", (3, 6): "error"},
}

STATE_PROPERTIES = (POSITION, STATUS)
SENSOR_PROPERTIES = {
    "battery_level": BATTERY_LEVEL,
    "charging_state": CHARGING_STATE,
    "battery_temperature": BATTERY_TEMPERATURE,
    "ambient_light": AMBIENT_LIGHT,
    "fault": FAULT,
    "run_time": RUN_TIME,
    "hold_state": HOLD_STATE,
}
READ_PROPERTIES = (
    STATE_PROPERTIES
    + tuple(SENSOR_PROPERTIES.values())
    + tuple(SWITCH_PROPERTIES.values())
)
READ_RANGES = {
    POSITION: range(101),
    STATUS: range(3),
    BATTERY_LEVEL: range(101),
    CHARGING_STATE: range(1, 4),
    BATTERY_TEMPERATURE: range(-5000, 10001),
    AMBIENT_LIGHT: range(3),
    FAULT: range(256),
    RUN_TIME: range(256),
    HOLD_STATE: range(4),
    MANUAL_START: range(2),
}
WRITE_RANGES = {
    MOTOR_CONTROL: range(3),
    TARGET_POSITION: range(101),
    MANUAL_START: range(2),
    WAKE_UP: (True,),
}
