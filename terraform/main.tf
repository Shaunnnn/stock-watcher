data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------------
# ECR — holds the container image GitHub Actions builds and pushes.
# Storage isn't in AWS's "always free" list (~$0.10/GB-month beyond any
# allowance), but one small Python image is a couple hundred MB, so
# realistically a few cents a month at most.
# ---------------------------------------------------------------------
resource "aws_ecr_repository" "app" {
  name                 = var.project_name
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep only the 10 most recent images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}

# ---------------------------------------------------------------------
# DynamoDB — watchlist.json / price_log.json / news_cache.json /
# filings_cache.json, one item each (see storage.py). PROVISIONED at 5
# RCU/5 WCU rather than on-demand, specifically to stay inside DynamoDB's
# always-free 25 RCU / 25 WCU allowance — on-demand billing has no such
# free allowance and would cost (tiny, but nonzero) per request.
# ---------------------------------------------------------------------
resource "aws_dynamodb_table" "data" {
  name         = "${var.project_name}-data"
  billing_mode = "PROVISIONED"
  read_capacity  = 5
  write_capacity = 5
  hash_key     = "file"

  attribute {
    name = "file"
    type = "S"
  }
}

# ---------------------------------------------------------------------
# S3 — embeddings_cache.json (a few MB, over DynamoDB's 400KB item
# limit). A few MB of storage costs a fraction of a cent a month even
# outside any free tier.
# ---------------------------------------------------------------------
resource "aws_s3_bucket" "data" {
  bucket = "${var.project_name}-data-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ---------------------------------------------------------------------
# IAM — least-privilege execution role: CloudWatch Logs (standard Lambda
# managed policy) + read/write on exactly this table and this bucket.
# ---------------------------------------------------------------------
resource "aws_iam_role" "lambda_exec" {
  name = "${var.project_name}-lambda-exec"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "basic_logs" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "data_access" {
  name = "${var.project_name}-data-access"
  role = aws_iam_role.lambda_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem"]
        Resource = aws_dynamodb_table.data.arn
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = "${aws_s3_bucket.data.arn}/*"
      }
    ]
  })
}

# ---------------------------------------------------------------------
# CloudWatch Logs — explicit group + retention so logs don't accumulate
# forever (CloudWatch Logs' always-free allowance is 5GB ingestion +
# 5GB storage; a retention window keeps this well inside that).
# ---------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "app" {
  name              = "/aws/lambda/${var.project_name}"
  retention_in_days = 14
}

# ---------------------------------------------------------------------
# Lambda — container image deploy. image_uri points at :latest, but
# after the FIRST apply, GitHub Actions owns pushing new images and
# repointing the function at them (via `aws lambda update-function-code`)
# — Terraform is told to ignore that field afterward so it doesn't fight
# CI over which image is "current".
# ---------------------------------------------------------------------
resource "aws_lambda_function" "app" {
  function_name = var.project_name
  role          = aws_iam_role.lambda_exec.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.app.repository_url}:latest"

  timeout      = 30
  memory_size  = 512
  architectures = ["x86_64"]

  environment {
    variables = {
      OPENAI_API_KEY   = var.openai_api_key
      APP_PASSWORD     = var.app_password
      EDGAR_USER_AGENT = var.edgar_user_agent
      DYNAMODB_TABLE   = aws_dynamodb_table.data.name
      S3_BUCKET        = aws_s3_bucket.data.id
    }
  }

  depends_on = [aws_cloudwatch_log_group.app]

  lifecycle {
    ignore_changes = [image_uri]
  }
}

# Public Function URL — no API Gateway, no ALB, so no extra per-request
# or flat monthly charge on top of Lambda itself (see cost notes in the
# README). auth_type NONE makes this a plain clickable public link, same
# as the NutriBot deploy; the app's own APP_PASSWORD gate (optional) is
# the safety net if you'd rather it not be wide open.
resource "aws_lambda_function_url" "app" {
  function_name      = aws_lambda_function.app.function_name
  authorization_type = "NONE"
}
