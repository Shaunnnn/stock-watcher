# ---------------------------------------------------------------------
# GitHub Actions OIDC — lets the deploy workflow assume an AWS role by
# presenting a short-lived token GitHub issues, instead of storing a
# long-lived AWS access key as a GitHub secret. The role's trust policy
# below restricts this to exactly one repo, pushes to main only.
# ---------------------------------------------------------------------
resource "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"

  client_id_list = ["sts.amazonaws.com"]

  # GitHub's OIDC root CA thumbprint (documented by GitHub/AWS; the
  # provider itself validates the cert chain, this is a legacy required
  # field the AWS provider still expects).
  # GitHub has rotated the intermediate CA behind this endpoint before,
  # which silently breaks an OIDC provider pinned to only the old
  # thumbprint (AssumeRoleWithWebIdentity then fails with a generic
  # 'not authorized' error even though the trust policy is correct).
  # Both currently-valid thumbprints are listed so a future rotation
  # doesn't break this again.
  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fcd",
  ]
}

resource "aws_iam_role" "github_actions" {
  name = "${var.project_name}-github-actions"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        }
        StringLike = {
          # GitHub started issuing an "immutable subject claim" for repos
          # created after 2026-07-15 — repo:owner@ownerID/repo@repoID:...
          # instead of the classic name-only repo:owner/repo:... — and
          # there's no way to tell which format a given repo uses without
          # trying it, so both are accepted here.
          "token.actions.githubusercontent.com:sub" = [
            "repo:${var.github_repo}:ref:refs/heads/main",
            "repo:${split("/", var.github_repo)[0]}@*/${split("/", var.github_repo)[1]}@*:ref:refs/heads/main",
          ]
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "github_actions_deploy" {
  name = "${var.project_name}-github-actions-deploy"
  role = aws_iam_role.github_actions.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:PutImage",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
          "ecr:BatchGetImage"
        ]
        Resource = aws_ecr_repository.app.arn
      },
      {
        Effect = "Allow"
        Action = [
          "lambda:UpdateFunctionCode",
          "lambda:GetFunction",
          "lambda:GetFunctionConfiguration"
        ]
        Resource = aws_lambda_function.app.arn
      }
    ]
  })
}
