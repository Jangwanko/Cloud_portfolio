resource "random_password" "db" {
  length  = 24
  special = false
}

resource "aws_security_group" "db" {
  name_prefix = "${var.name_prefix}-rds-"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = var.allowed_security_group_ids
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

module "db" {
  source  = "terraform-aws-modules/rds/aws"
  version = "~> 6.6"

  identifier = "${var.name_prefix}-postgres"

  engine               = "postgres"
  engine_version       = "16.3"
  family               = "postgres16"
  major_engine_version = "16"
  instance_class       = var.instance_class

  allocated_storage     = var.allocated_storage
  max_allocated_storage = var.allocated_storage * 2

  db_name  = var.db_name
  username = var.username
  password = random_password.db.result
  port     = 5432

  multi_az               = true
  storage_encrypted      = true
  backup_retention_period = var.backup_retention_period
  deletion_protection    = false
  skip_final_snapshot    = true

  create_db_subnet_group = true
  subnet_ids             = var.subnet_ids

  vpc_security_group_ids = [aws_security_group.db.id]
}
