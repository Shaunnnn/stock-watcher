variable "aws_region" {
  description = "AWS region to deploy into. us-east-1 is a safe default (cheapest, most services available first)."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Prefix used for every resource name, so this can coexist with other projects in the same AWS account."
  type        = string
  default     = "stock-watcher"
}

variable "openai_api_key" {
  description = "OpenAI API key, passed to Lambda as an environment variable. Set via TF_VAR_openai_api_key or a git-ignored terraform.tfvars — never commit this."
  type        = string
  sensitive   = true
}

variable "app_password" {
  description = "Optional Basic Auth password for the deployed app (maps to APP_PASSWORD). Leave blank to leave the app open — fine while auth_type is AWS_IAM, but set this if you ever switch the Function URL to NONE (public)."
  type        = string
  sensitive   = true
  default     = ""
}

variable "edgar_user_agent" {
  description = "SEC EDGAR requires a descriptive User-Agent identifying who's making requests (see filings_fetch.py). Format: \"Your Name your.email@example.com\"."
  type        = string
}

variable "github_repo" {
  description = "GitHub repo allowed to assume the deploy role, as owner/repo (e.g. Shaunnnn/stock-watcher). Scopes OIDC trust to exactly this repo's main branch — nothing else can assume this role."
  type        = string
}
