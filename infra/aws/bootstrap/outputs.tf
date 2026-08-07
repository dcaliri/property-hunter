output "state_bucket" {
  value       = aws_s3_bucket.state.id
  description = "S3 bucket holding the main stack's Terraform state"
}

output "data_volume_id" {
  value       = aws_ebs_volume.data.id
  description = "Retained EBS volume id (app SQLite)"
}

output "data_volume_size_gb" {
  value       = aws_ebs_volume.data.size
  description = "Retained EBS volume size in GiB"
}

output "availability_zone" {
  value       = aws_ebs_volume.data.availability_zone
  description = "AZ the volume lives in; every main stack must launch here"
}

output "ecr_repository_url" {
  value       = aws_ecr_repository.app.repository_url
  description = "ECR repository holding deployed app images"
}
