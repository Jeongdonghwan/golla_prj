#!/usr/bin/env bash
# 골라봄(shopa_prj) 한 번에 설치·오픈 (211 서버, root). 다시 실행해도 된다 — 이미 된 단계는 건너뛰고, 열린 페이지는 닫지 않는다.
#   비공개 저장소: git clone https://<토큰>@github.com/<계정>/<저장소>.git /var/www/shopa_prj && THEN=<하루 공개 수> bash /var/www/shopa_prj/deploy/install.sh golrabom.kr [메일]
#   공개 저장소:   THEN=<하루 공개 수> bash <(curl -fsSL https://raw.githubusercontent.com/<저장소>/main/deploy/install.sh) golrabom.kr [메일]
# 하는 일: 패키지 → 코드 → venv → .env → DB(덤프) → 서비스 → DNS 확인 → nginx+HTTPS → 첫 오픈(stage·plan·publish) → cron → SEO 대시보드 연결 → 점검
set -euo pipefail
D="${1:?사용법: THEN=하루공개수 bash <(curl ...) golrabom.kr [메일]}"; MAIL="${2:-}"
SUBS="heat air clean kitchen beauty digital life"; DIR=/var/www/shopa_prj; REPO="${REPO:-https://github.com/__OWNER__/__REPO__.git}"
say() { printf '\n== %s\n' "$*"; }

say "1/10 패키지"
apt-get install -y -q python3-venv python3-pip fonts-nanum certbot python3-certbot-nginx >/dev/null && echo OK

say "2/10 코드"
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull -q && echo "업데이트"; else git clone -q "$REPO" "$DIR" && echo "받음"; fi
cd "$DIR"
if [ ! -f .port ]; then P=8060; while ss -tln | grep -q ":$P "; do P=$((P+1)); done; echo $P > .port; fi
P=$(cat .port); echo "포트 $P"
[ -x venv/bin/gunicorn ] || python3 -m venv venv
./venv/bin/pip install -q -r requirements.txt && echo "pip OK"

say "3/10 환경(.env)"
if [ ! -f .env ]; then
  cp .env.example .env
  sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$(openssl rand -hex 24)|; s|^DB_PORT=.*|DB_PORT=3306|; s|^DB_USER=.*|DB_USER=shopa_prj|; s|^DB_PASSWORD=.*|DB_PASSWORD=$(openssl rand -hex 12)|; s|^DB_NAME=.*|DB_NAME=shopa_prj|; s|^ADMIN_PASSWORD=.*|ADMIN_PASSWORD=$(openssl rand -hex 6)|" .env
fi
sed -i "s|^BASE_URL=.*|BASE_URL=https://heat.$D|" .env
grep -q '^OG_FONT=.' .env || { sed -i '/^OG_FONT=/d' .env; echo "OG_FONT=/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf" >> .env; }
DBPW=$(grep '^DB_PASSWORD=' .env | cut -d= -f2-)
echo OK

say "4/10 DB"
if mysql -e "SELECT 1" >/dev/null 2>&1; then ROOT=(mysql); else echo "MariaDB root 비밀번호:"; read -rs RPW; ROOT=(mysql -u root "-p$RPW"); fi
"${ROOT[@]}" -e "CREATE DATABASE IF NOT EXISTS shopa_prj DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci; CREATE USER IF NOT EXISTS 'shopa_prj'@'localhost'; ALTER USER 'shopa_prj'@'localhost' IDENTIFIED BY '$DBPW'; GRANT ALL PRIVILEGES ON shopa_prj.* TO 'shopa_prj'@'localhost'; FLUSH PRIVILEGES;"
M=(mysql -u shopa_prj "-p$DBPW" shopa_prj)
if ! "${M[@]}" -e "SELECT 1 FROM keyword_combos LIMIT 1" >/dev/null 2>&1; then
  echo "덤프 적재 중…"; gunzip -c deploy/shopa_prj.sql.gz | "${M[@]}"
fi
"${M[@]}" -e "UPDATE sites SET host=CONCAT(SUBSTRING_INDEX(host,'.',1),'.$D'), indexnow_key=IFNULL(indexnow_key, MD5(RAND()));"
"${M[@]}" -e "SELECT host, (SELECT COUNT(*) FROM keyword_combos k WHERE k.site_id=s.id) pages FROM sites s;"

say "5/10 서비스"
cat > /etc/systemd/system/shopa_prj.service <<EOF
[Unit]
Description=shopa_prj golrabom Flask (gunicorn :$P)
After=network.target mariadb.service

[Service]
WorkingDirectory=$DIR
ExecStart=$DIR/venv/bin/gunicorn -w 2 --threads 4 --timeout 60 -b 127.0.0.1:$P "app:create_app()"
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload && systemctl enable -q shopa_prj && systemctl restart shopa_prj && sleep 2 && systemctl is-active shopa_prj

say "6/10 DNS 확인"
IPS=" $(hostname -I) 211.45.175.195 "; MISS=""
for s in $SUBS; do
  ip=$(getent hosts "$s.$D" | cut -d' ' -f1 || true); echo "$s.$D → ${ip:-없음}"
  if [ -z "$ip" ] || [[ "$IPS" != *" $ip "* ]]; then MISS="$MISS $s"; fi
done
if [ -n "$MISS" ]; then
  echo "아직 이 서버로 연결되지 않은 주소:$MISS — DNS 저장 후 10~30분 뒤 같은 명령을 다시 실행하세요(여기까지는 그대로 남습니다)."; exit 1
fi

say "7/10 nginx + HTTPS"
bash deploy/nginx_sites.sh "$D" "$P"
CERT=(certbot --nginx --agree-tos --redirect -n --expand)
[ -n "$MAIL" ] && CERT+=(-m "$MAIL") || CERT+=(--register-unsafely-without-email)
for s in $SUBS; do CERT+=(-d "$s.$D"); done
"${CERT[@]}" 2>&1 | grep -E "Congratulations|Successfully|Certificate not yet due|Keeping|error|Error" || true
systemctl restart shopa_prj && sleep 2

say "8/10 첫 오픈"
F="./venv/bin/flask --app app jobs"
$F og-kinds >/dev/null 2>&1 || echo "(OG 이미지 생성 경고 — 페이지에는 영향 없음)"
if [ -z "$("${M[@]}" -N -e "SELECT 1 FROM site_settings WHERE \`key\`='publish_plan' LIMIT 1")" ]; then
  if [ -z "${THEN:-}" ]; then echo "THEN(둘째 날부터 하루 공개 수)이 없습니다. THEN=숫자 를 앞에 붙여 다시 실행하세요."; exit 1; fi
  $F stage --yes && $F plan "$(date +%F)" --then "$THEN"
else
  echo "공개 계획이 이미 있음 — 다시 닫지 않고 오늘 몫만 확인"
fi
$F publish --quiet && $F export-sitemaps export && $F indexnow
cp deploy/cron.txt /etc/cron.d/shopa_prj && chmod 644 /etc/cron.d/shopa_prj && echo "cron 설치(매일 05:40 공개)"

say "9/10 SEO 대시보드 연결"
# 수집기(j_dash 노드)는 /var/lib/seo-dash/only 의 호스트만 본다. golrabom 7개를 더한다(기존 사이트는 그대로).
if [ -f /var/lib/seo-dash/only ]; then
  ONLY="$(cat /var/lib/seo-dash/only)"
  for s in $SUBS; do case " $ONLY " in *" $s.$D "*) ;; *) ONLY="$ONLY $s.$D";; esac; done
  ONLY="$(echo $ONLY)"; printf '%s' "$ONLY" > /var/lib/seo-dash/only
  [ -f /etc/cron.d/seo-dash ] && sed -i "s|DASH_ONLY=\"[^\"]*\"|DASH_ONLY=\"$ONLY\"|" /etc/cron.d/seo-dash
  echo "대시보드 수집 대상: $ONLY"
  (DASH_ONLY="$ONLY" python3 /opt/seo-dash/dash.py --push "$(grep -o 'push [^ ]*' /etc/cron.d/seo-dash | head -1 | cut -d' ' -f2)" >/dev/null 2>&1 && echo "첫 수집 전송 OK") || echo "(첫 수집은 다음 정기 실행 07:20/15:20 에)"
else
  echo "이 서버에 대시보드 수집기가 없습니다 — deploy/DEPLOY.md '대시보드' 블록으로 설치하세요."
fi

say "10/10 점검"
for s in $SUBS; do curl -s -o /dev/null -w "https://$s.$D/ %{http_code}\n" "https://$s.$D/"; done
curl -s "https://heat.$D/robots.txt" | grep -i sitemap
echo; echo "관리자: https://heat.$D/admin/  아이디 $(grep '^ADMIN_USER=' .env | cut -d= -f2)  비밀번호 $(grep '^ADMIN_PASSWORD=' .env | cut -d= -f2)"
echo "완료. 오늘 연 수는 위 publish 줄, 앞으로의 일정은 plan 줄에 있습니다."
