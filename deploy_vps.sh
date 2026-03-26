#!/bin/bash
# ============================================================
# TITAN TRADER — VPS Deploy Script
# ============================================================
# Run this once on a fresh Ubuntu VPS ($6/mo on DigitalOcean)
# to set up the intraday watchdog as a persistent service.
#
# Usage: bash deploy_vps.sh
# ============================================================

set -e

echo "⚡ TITAN TRADER VPS SETUP"
echo "========================="

# Update system
apt-get update -qq
apt-get install -y python3-pip python3-venv git cron

# Clone your repo
if [ ! -d "/opt/titan-trader" ]; then
    git clone https://github.com/YOUR_USERNAME/titan-trader.git /opt/titan-trader
fi
cd /opt/titan-trader

# Create virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Create environment file
cat > /opt/titan-trader/.env << 'ENVFILE'
ALPACA_API_KEY=your_key_here
ALPACA_SECRET_KEY=your_secret_here
ALPACA_PAPER=true
ANTHROPIC_API_KEY=your_key_here
SUPABASE_URL=your_url_here
SUPABASE_KEY=your_key_here
TWILIO_ACCOUNT_SID=your_sid_here
TWILIO_AUTH_TOKEN=your_token_here
TWILIO_FROM_NUMBER=+1xxxxxxxxxx
NOTIFICATION_PHONE=+15167840478
NOTIFICATION_EMAIL=tylerzar24@gmail.com
SMTP_USER=tylerzar24@gmail.com
SMTP_PASS=your_app_password
GOOGLE_SHEETS_ID=your_sheet_id
GOOGLE_SHEETS_API_KEY=your_key
ENVFILE

echo "⚠️  Edit /opt/titan-trader/.env with your real credentials before continuing."

# Create systemd service for watchdog (runs during market hours)
cat > /etc/systemd/system/titan-watchdog.service << 'SERVICE'
[Unit]
Description=Titan Trader Intraday Watchdog
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/titan-trader
EnvironmentFile=/opt/titan-trader/.env
ExecStart=/opt/titan-trader/venv/bin/python watchdog.py
Restart=on-failure
RestartSec=60

[Install]
WantedBy=multi-user.target
SERVICE

# Cron jobs for pre-market, trading session, post-market
# These run on the VPS itself (no GitHub Actions needed for these)
(crontab -l 2>/dev/null; echo "
# Titan Trader — Mon-Fri only
# Pre-market scan: 8:00 AM ET
0 13 * * 1-5 cd /opt/titan-trader && source venv/bin/activate && RUN_MODE=pre_market $(cat .env | xargs) python main.py >> /var/log/titan-pre-market.log 2>&1

# Trading session: 9:35 AM ET
35 14 * * 1-5 cd /opt/titan-trader && source venv/bin/activate && RUN_MODE=trade TRADE_MODE=trade $(cat .env | xargs) python main.py >> /var/log/titan-trading.log 2>&1

# Post-market: 5:00 PM ET
0 22 * * 1-5 cd /opt/titan-trader && source venv/bin/activate && RUN_MODE=post_market $(cat .env | xargs) python main.py >> /var/log/titan-post-market.log 2>&1

# Start watchdog at 9:29 AM ET (1 min before open)
29 14 * * 1-5 systemctl start titan-watchdog

# Stop watchdog at 4:16 PM ET
16 21 * * 1-5 systemctl stop titan-watchdog
") | crontab -

systemctl daemon-reload
systemctl enable titan-watchdog

echo ""
echo "✅ Setup complete."
echo ""
echo "Next steps:"
echo "  1. Edit /opt/titan-trader/.env with your credentials"
echo "  2. Test: cd /opt/titan-trader && source venv/bin/activate && RUN_MODE=pre_market python main.py"
echo "  3. Test watchdog: python watchdog.py"
echo "  4. Cron jobs are set — system will run automatically on market days"
echo ""
echo "Logs:"
echo "  Pre-market:  tail -f /var/log/titan-pre-market.log"
echo "  Trading:     tail -f /var/log/titan-trading.log"
echo "  Post-market: tail -f /var/log/titan-post-market.log"
echo "  Watchdog:    journalctl -u titan-watchdog -f"
