# Replau Kitchen UI v3

Kitchen board for WhatsApp orders.

## URL

```text
http://127.0.0.1:8791
```

## Color logic

- GREEN: 0-20 min
- YELLOW: more than 20 min
- RED: more than 30 min

## Install SQL

```bash
mkdir -p ~/postgrest-local/replau_kitchen_ui
cp add_kitchen_ui.sql kitchen_ui.py requirements.txt .env.example replau-kitchen-ui.service test_kitchen_ui.sh README.md ~/postgrest-local/replau_kitchen_ui/
cd ~/postgrest-local/replau_kitchen_ui

sudo -u postgres psql -v ON_ERROR_STOP=1 -d localapi < add_kitchen_ui.sql
sudo -u postgres psql -d localapi -c "NOTIFY pgrst, 'reload schema';"
sudo systemctl restart postgrest
```

## Install app

```bash
sudo mkdir -p /opt/replau_kitchen_ui
sudo chown -R "$USER:$USER" /opt/replau_kitchen_ui

cp kitchen_ui.py requirements.txt .env.example replau-kitchen-ui.service /opt/replau_kitchen_ui/

cd /opt/replau_kitchen_ui
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
chmod +x kitchen_ui.py
```

## Environment

```bash
sudo cp .env.example /etc/replau-kitchen-ui.env
sudo nano /etc/replau-kitchen-ui.env
sudo chmod 600 /etc/replau-kitchen-ui.env
sudo chown root:root /etc/replau-kitchen-ui.env
```

## Run manually

```bash
sudo bash -c '
set -a
source /etc/replau-kitchen-ui.env
set +a
cd /opt/replau_kitchen_ui
/opt/replau_kitchen_ui/.venv/bin/python /opt/replau_kitchen_ui/kitchen_ui.py
'
```

## Service

```bash
sudo cp /opt/replau_kitchen_ui/replau-kitchen-ui.service /etc/systemd/system/replau-kitchen-ui.service
sudo systemctl daemon-reload
sudo systemctl enable replau-kitchen-ui
sudo systemctl start replau-kitchen-ui
sudo systemctl status replau-kitchen-ui --no-pager
```

## Test

```bash
curl http://127.0.0.1:8791/health | jq
curl http://127.0.0.1:8791/api/orders | jq
```
