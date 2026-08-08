terraform {
  required_version = ">= 1.7"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

# --- Network (T014) ---------------------------------------------------------

resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "property-hunter-vpc"
  }
}

resource "aws_subnet" "main" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.0.0/24"
  availability_zone       = var.az
  map_public_ip_on_launch = var.assign_public_ip

  tags = {
    Name = "property-hunter-subnet"
  }
}

# Internet gateway + default route: the instance needs OUTBOUND egress for SSM,
# ECR, package installs, and the app's own collection traffic. No inbound rules
# exist, so the gateway is never used to reach the instance.
resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "property-hunter-igw"
  }
}

resource "aws_route_table" "main" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = {
    Name = "property-hunter-rt"
  }
}

resource "aws_route_table_association" "main" {
  subnet_id      = aws_subnet.main.id
  route_table_id = aws_route_table.main.id
}

# Security group: only 80/443 reach the Caddy gateway (HTTPS + Let's Encrypt
# HTTP-01 challenge). The dashboard itself stays bound to the internal network
# behind Caddy's Basic Auth; SSM needs no inbound rules. Default egress allowed.
# NOTE: the description below must stay as-is — aws_security_group.description is
# ForceNew, so editing it would replace the SG and briefly drop instance traffic.
resource "aws_security_group" "main" {
  name        = "property-hunter"
  description = "No inbound rules; outbound egress only"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "property-hunter-sg"
  }
}

# --- Identity + bootstrapping (T015) ----------------------------------------

data "aws_iam_policy_document" "instance_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "instance" {
  name               = "property-hunter-role"
  assume_role_policy = data.aws_iam_policy_document.instance_assume.json

  tags = {
    Name = "property-hunter-role"
  }
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "ecr_read" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

# Allow the instance to read /property-hunter/* from SSM Parameter Store and
# decrypt the SecureString values (default aws/ssm KMS key).
data "aws_iam_policy_document" "ssm_params" {
  statement {
    effect = "Allow"
    actions = [
      "ssm:GetParameter",
      "ssm:GetParameters",
      "ssm:GetParametersByPath",
    ]
    resources = [
      "arn:aws:ssm:${var.region}:*:parameter/property-hunter",
      "arn:aws:ssm:${var.region}:*:parameter/property-hunter/*",
    ]
  }
  statement {
    effect = "Allow"
    actions = [
      "kms:Decrypt",
    ]
    resources = ["arn:aws:kms:${var.region}:*:alias/aws/ssm"]
  }
}

resource "aws_iam_policy" "ssm_params" {
  name        = "property-hunter-ssm-params"
  description = "Read access to /property-hunter/* SSM parameters"
  policy      = data.aws_iam_policy_document.ssm_params.json
}

resource "aws_iam_role_policy_attachment" "ssm_params" {
  role       = aws_iam_role.instance.name
  policy_arn = aws_iam_policy.ssm_params.arn
}

resource "aws_iam_instance_profile" "instance" {
  name = "property-hunter-profile"
  role = aws_iam_role.instance.name
}

# --- Compute + data wiring (T016) -------------------------------------------

resource "aws_instance" "app" {
  ami                    = data.aws_ami.al2023_arm64.id
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.main.id
  vpc_security_group_ids = [aws_security_group.main.id]
  iam_instance_profile   = aws_iam_instance_profile.instance.name

  # No key_name: deploys and access go through SSM (research §7/§8).
  user_data = templatefile("${path.module}/user_data.tpl", {
    remote_deploy_script = file("${path.module}/../../scripts/cloud/remote/remote-deploy.sh")
    app_image            = var.app_image
    data_dir             = var.data_dir
    region               = var.region
  })

  root_block_device {
    volume_type = "gp3"
  }

  # user_data is the first-boot bootstrap only: redeploys ship the current
  # remote-deploy.sh over SSM (deploy.sh), so edits to it must NOT force an
  # instance replacement (which would churn the public IP / DNS).
  lifecycle {
    ignore_changes = [user_data]
  }

  tags = {
    Name = "property-hunter"
    Ref  = var.app_ref
  }
}

# Attach the retained volume. Destroying this only detaches (the volume is a
# data source, so it can never be deleted from this state).
resource "aws_volume_attachment" "data" {
  device_name = var.volume_device
  volume_id   = data.aws_ebs_volume.data.id
  instance_id = aws_instance.app.id
}
