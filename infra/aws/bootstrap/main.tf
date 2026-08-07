terraform {
  required_version = ">= 1.7"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
  backend "local" {
    path = "terraform.tfstate"
  }
}

variable "bucket" {
  type        = string
  description = "Globally unique S3 bucket name holding the main stack's state"
}

variable "az" {
  type        = string
  description = "Availability zone of the retained EBS volume (all cycles use it)"
}

variable "region" {
  type        = string
  description = "AWS region (derived from the AZ)"
}

variable "volume_size" {
  type        = number
  default     = 20
  description = "Retained EBS gp3 size in GiB (research §2)"
}

provider "aws" {
  region = var.region
}

# --- Retained resources (research §4/§5). `down` never removes these. ---

# S3 bucket: Terraform state for the ephemeral main stack (versioned, SSE).
resource "aws_s3_bucket" "state" {
  bucket        = var.bucket
  force_destroy = false

  tags = {
    Name = "property-hunter-state"
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# EBS volume: the app's SQLite lives here and survives every cycle.
# Referenced by the main stack only via a data source, never a managed resource.
resource "aws_ebs_volume" "data" {
  availability_zone = var.az
  size              = var.volume_size
  type              = "gp3"

  tags = {
    Name        = "property-hunter-data"
    StateBucket = var.bucket
  }

  lifecycle {
    prevent_destroy = true
  }
}

# ECR repository: deploy artifact target (PROMPT decision: deploy by image, no
# git checkout on the instance). Retained so old image tags stay pullable.
resource "aws_ecr_repository" "app" {
  name                 = "property-hunter"
  image_tag_mutability = "MUTABLE"
  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name = "property-hunter-images"
  }

  lifecycle {
    prevent_destroy = true
  }
}
