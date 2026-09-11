(export $(grep -v '^#' .env | xargs) && \
pysonar \
  --sonar-token=$SONAR_TOKEN \
  --sonar-host-url=$SONAR_HOST_URL \
  --sonar-project-key=monmon \
  #--verbose \
)