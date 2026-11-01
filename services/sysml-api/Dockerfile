# SysML v2 API & Services: the OMG pilot implementation (Systems-Modeling/SysML-v2-API-Services).
#
# By default it is built from source (sbt; the first build takes a while). To reuse an
# image you already built from the same release, pass its name, which skips that stage:
#   SYSML_API_PILOT_IMAGE=sysmlapiserver:latest docker compose build sysml-api
# (the image must hold the distribution under /usr/src/myapp/scripts, as the build stage does).
ARG PILOT_IMAGE=source

FROM amazoncorretto:11-al2023-jdk AS source
RUN dnf install -y -q tar gzip unzip && dnf clean all
ARG SBT_VERSION=1.2.8
ARG RELEASE=2025-02
WORKDIR /build
RUN curl -fsSL https://github.com/sbt/sbt/releases/download/v${SBT_VERSION}/sbt-${SBT_VERSION}.tgz | tar xz \
 && curl -fsSL https://github.com/Systems-Modeling/SysML-v2-API-Services/archive/${RELEASE}.tar.gz | tar xz
WORKDIR /build/SysML-v2-API-Services-${RELEASE}
RUN /build/sbt/bin/sbt -batch dist \
 && mkdir -p /usr/src/myapp/scripts \
 && unzip -q target/universal/sysml-v2-api-services-${RELEASE}.zip -d /tmp/dist \
 && mv /tmp/dist/sysml-v2-api-services-${RELEASE}/* /usr/src/myapp/scripts/

FROM ${PILOT_IMAGE} AS pilot

FROM amazoncorretto:11-al2023-headless
COPY --from=pilot /usr/src/myapp/scripts /opt/sysml-api
COPY entrypoint.sh CreateSchema.java /opt/sysml-api/
RUN chmod +x /opt/sysml-api/entrypoint.sh /opt/sysml-api/bin/sysml-v2-api-services \
 && mkdir -p /opt/sysml-api/logs && chown -R 10001 /opt/sysml-api/conf /opt/sysml-api/logs
USER 10001
WORKDIR /opt/sysml-api
EXPOSE 9000
ENTRYPOINT ["/opt/sysml-api/entrypoint.sh"]
