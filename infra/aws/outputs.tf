output "instance_id" {
  value       = aws_instance.app.id
  description = "EC2 instance id of the running environment"
}

output "instance_type" {
  value       = aws_instance.app.instance_type
  description = "EC2 instance type"
}

output "availability_zone" {
  value       = aws_instance.app.availability_zone
  description = "AZ the instance was launched in (pinned to the volume's AZ)"
}

output "app_ref" {
  value       = var.app_ref
  description = "Git ref deployed at this cycle"
}

output "app_image" {
  value       = var.app_image
  description = "ECR image deployed at this cycle"
}

output "dashboard_command" {
  value       = "scripts/cloud/dashboard.sh --instance ${aws_instance.app.id}"
  description = "Command that opens the SSM port-forward tunnel to the dashboard"
}
