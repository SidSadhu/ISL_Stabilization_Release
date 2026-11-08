# Local Mosquitto broker (development only)

`mosquitto-local.conf` binds to `127.0.0.1:1883` and allows anonymous clients strictly for an isolated local demo. It must not be reused for LAN/hardware or internet deployment. Later ESP32 tests require an authenticated broker, restricted topic ACLs, and firewall rules.

On Windows with Mosquitto installed, from the project folder run:

```powershell
mosquitto -c .\broker\mosquitto-local.conf -v
```

Keep this terminal open. Start `python .\scripts\run_mqtt_simulator.py` in a second terminal. Start the FastAPI app in a third terminal. If the command topic or acknowledgement topic is changed in `.env`, use the same values in every participant.
