output "function_url" {
  description = "Public URL for the deployed app — this is what you'd put in the portfolio/README, same role as the NutriBot Render URL."
  value       = aws_lambda_function_url.app.function_url
}

output "ecr_repository_url" {
  description = "Push built images here (docker push <this>:latest). GitHub Actions uses this too."
  value       = aws_ecr_repository.app.repository_url
}

output "dynamodb_table_name" {
  value = aws_dynamodb_table.data.name
}

output "s3_bucket_name" {
  value = aws_s3_bucket.data.id
}

output "lambda_function_name" {
  value = aws_lambda_function.app.function_name
}

output "github_actions_role_arn" {
  description = "Put this in the repo's GitHub Actions secret AWS_ROLE_ARN."
  value       = aws_iam_role.github_actions.arn
}
