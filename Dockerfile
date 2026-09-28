# Container image for AWS Lambda, built around the AWS Lambda Web Adapter
# (https://github.com/awslabs/aws-lambda-web-adapter) rather than a
# hand-written Lambda handler. The adapter is a Lambda extension that
# translates Function URL requests into normal HTTP calls against a
# regular web server — so this is the exact same gunicorn/Flask app as
# local dev and the NutriBot deploy, completely unmodified for Lambda.
FROM public.ecr.aws/awsguru/aws-lambda-adapter:0.8.4 AS lambda-adapter

FROM python:3.11-slim

# The adapter binary — Lambda auto-loads any extension dropped in
# /opt/extensions at cold start.
COPY --from=lambda-adapter /lambda-adapter /opt/extensions/lambda-adapter

# Tell the adapter which local port the app actually listens on.
ENV AWS_LWA_PORT=8000
ENV PORT=8000

WORKDIR /var/task

COPY requirements.txt requirements-lambda.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-lambda.txt

COPY . .

# Same command shape as the NutriBot deploy: gunicorn serving the Flask
# app object directly. --workers 1 because Lambda already gives each
# concurrent request its own execution environment; more workers here
# would just compete for the same 512MB/small vCPU allotment.
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4", "--timeout", "60"]
