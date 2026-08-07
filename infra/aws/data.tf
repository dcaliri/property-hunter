# Retained EBS volume looked up by tag. It is a data source here, never a
# managed resource — `terraform destroy` of this stack can only DETACH it, and
# deleting the volume is impossible from this state (research §4).
data "aws_ebs_volume" "data" {
  filter {
    name   = "tag:Name"
    values = ["property-hunter-data"]
  }
}

# Current Amazon Linux 2023 ARM64 AMI (matches the Graviton t4g instance).
data "aws_ami" "al2023_arm64" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-arm64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }

  filter {
    name   = "architecture"
    values = ["arm64"]
  }
}
