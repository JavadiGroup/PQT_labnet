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

# One line per simulated Lab PC:
#   <lab-pc id>  <gRPC port>  <health port>  <inventory file, relative to the repo>
#
# The id becomes both LABNET_LAB_PC_ID and the Central device_manager username.
# The inventory's create_devices() decides which devices that Lab PC serves.
# Device IDs must be unique across all Lab PCs.
LAB_PCS=(
    "lab-pc-dev-01 50051 9001 scripts/dev/inventories/dev_lab_pc_01.py"
    "lab-pc-dev-02 50052 9002 scripts/dev/inventories/dev_lab_pc_02.py"
)

DEV_DATABASE="$DEVNET_DIR/central.db"
