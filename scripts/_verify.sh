echo "--- persistence logs"
sudo -n journalctl -u ai-classroom --since "5 min ago" --no-pager | grep -E "persistence|locked|Traceback" | tail -15
echo "--- checkpoints db"
ls -l /opt/ai-classroom/data/
echo "--- pm2 startup"
systemctl list-unit-files | grep -i pm2 || echo no-pm2-unit
