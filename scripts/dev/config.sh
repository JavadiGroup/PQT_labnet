# Local development network topology. Edit this file to change what
# scripts/dev/seed.sh creates and scripts/dev/up.sh starts.
#
# Everything binds to 127.0.0.1 and uses plaintext, which LabNet permits only
# on loopback. Nothing here is reachable from another computer.

CENTRAL_PORT=8008
CENTRAL_URL="http://127.0.0.1:$CENTRAL_PORT"

# Central users created by seed.sh. Each gets .devnet/<name>.env; point a
# client at one with LABNET_ENV_FILE. Lab PC users come from LAB_PCS below.
ADMIN_USER="dev-admin"
RESEARCHER_USER="researcher"

# The PQT Lab Control Center web app: a LabNet client with its own Central
# user. In development it serves http://127.0.0.1:$CONTROL_CENTER_PORT/status.
CONTROL_CENTER_USER="control-center"
CONTROL_CENTER_PORT=8080

# The safe-state watchdog for Automation device grants: its own Central user
# (it takes leases to apply a grant's safe state if the Control Center dies),
# and a loopback health page at http://127.0.0.1:$WATCHDOG_PORT/health/ready.
WATCHDOG_USER="automation-watchdog"
WATCHDOG_PORT=8081

# One line per simulated Lab PC:
#   <lab-pc id>  <gRPC port>  <health port>  <inventory: package.module:function>
#
# The id becomes both LABNET_LAB_PC_ID and the Central device_manager username.
# The inventory's create_devices() decides which devices that Lab PC serves.
# Device IDs must be unique across all Lab PCs.
LAB_PCS=(
    "lab-pc-dev-01 50051 9001 labnet_sim.inventories.dev_lab_pc_01:create_devices"
    "lab-pc-dev-02 50052 9002 labnet_sim.inventories.dev_lab_pc_02:create_devices"
)

DEV_DATABASE="$DEVNET_DIR/central.db"
