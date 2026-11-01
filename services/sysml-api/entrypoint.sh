#!/bin/sh
# Point the pilot at the database given by the environment, keep its tables between
# restarts (the release default is create-drop) and accept any Host header.
set -e
: "${SYSML_API_DB_URL:=jdbc:postgresql://postgres:5432/sysml}"
: "${SYSML_API_DB_USER:=sysml}"
: "${SYSML_API_DB_PASSWORD:=sysml}"
: "${SYSML_API_DB_SCHEMA:=sysml_api}"
: "${SYSML_API_SECRET:=sysml-v2-api-services-secret-key-change-me}"

xml() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/"/\&quot;/g' -e 's/</\&lt;/g' -e 's/[\\#&]/\\&/g'; }
prop() { sed -i -e "s#<property name=\"$1\" value=\"[^\"]*\"/>#<property name=\"$1\" value=\"$(xml "$2")\"/>#" "$conf"; }

conf=conf/META-INF/persistence.xml
prop javax.persistence.jdbc.url "$SYSML_API_DB_URL"
prop javax.persistence.jdbc.user "$SYSML_API_DB_USER"
prop javax.persistence.jdbc.password "$SYSML_API_DB_PASSWORD"
prop hibernate.hbm2ddl.auto update
prop hibernate.show_sql false
prop hibernate.format_sql false
grep -q hibernate.default_schema "$conf" || sed -i -e "s#</properties>#    <property name=\"hibernate.default_schema\" value=\"$(xml "$SYSML_API_DB_SCHEMA")\"/>\n        </properties>#" "$conf"

java -cp "$(ls lib/org.postgresql.postgresql-*.jar)" CreateSchema.java \
  "$SYSML_API_DB_URL" "$SYSML_API_DB_USER" "$SYSML_API_DB_PASSWORD" "$SYSML_API_DB_SCHEMA"

rm -f RUNNING_PID
exec bin/sysml-v2-api-services \
  -Dplay.http.secret.key="$SYSML_API_SECRET" \
  -Dplay.filters.hosts.allowed.0=. \
  -Dpidfile.path=/dev/null \
  "$@"
