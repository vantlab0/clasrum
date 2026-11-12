echo "--- response headers via localhost"
curl -s -D - -o /dev/null -H 'Host: realtime.avelut.xyz' http://127.0.0.1/health
echo "--- default site"
sed -n '1,60p' /etc/nginx/sites-available/default
echo "--- nginx.conf includes"
grep -nE 'include|server_name|return 301' /etc/nginx/nginx.conf /etc/nginx/sites-enabled/* 2>/dev/null | head -30
