variable "region" {
  type        = string
  description = "AWS region for the ephemeral stack (volume's region)"
}

variable "az" {
  type        = string
  description = "Availability zone; pinned to the retained volume's AZ (EBS is AZ-bound)"
}

variable "state_bucket" {
  type        = string
  description = "S3 state bucket from the bootstrap state (research §5)"
}

variable "instance_type" {
  type        = string
  default     = "t4g.small"
  description = "EC2 instance type (t4g.small free-trial; t4g.micro fallback, research §1)"
}

variable "app_ref" {
  type        = string
  default     = "HEAD"
  description = "Git ref deployed at this cycle (rollback key, research §9)"
}

variable "app_image" {
  type        = string
  default     = ""
  description = "Full ECR image URI (account.dkr.ecr.<region>.amazonaws.com/property-hunter:<tag>)"
}

variable "data_dir" {
  type        = string
  default     = "/opt/property-hunter/data"
  description = "Host directory where the retained EBS volume is mounted"
}

variable "volume_device" {
  type        = string
  default     = "/dev/sdf"
  description = "Attachment device name for the retained volume"
}

variable "assign_public_ip" {
  type        = bool
  default     = true
  description = "Auto-assign a public IPv4 for outbound egress (SSM, ECR, app traffic). No inbound rules, so nothing is exposed. See research §8 for the cost note."
}
