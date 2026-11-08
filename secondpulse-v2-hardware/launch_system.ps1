# Start hardware daemon on COM18
$env:SERIAL_PORT = "COM18"
$env:BAUD_RATE = "115200"
Start-Process -NoNewWindow python -ArgumentList "d:\Projects\Climathon\secondpulse-v2-hardware\backend\hardware_daemon.py"
# Start Streamlit dashboard
Start-Process python -ArgumentList "-m streamlit run d:\Projects\Climathon\secondpulse-v2-hardware\frontend\app.py --server.port 8501"

