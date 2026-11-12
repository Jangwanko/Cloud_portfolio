data "aws_route53_zone" "this" {
  count        = var.certificate_mode == "dns" ? 1 : 0
  name         = var.zone_name
  private_zone = false
}

resource "aws_acm_certificate" "this" {
  domain_name               = var.certificate_mode == "dns" ? var.domain_name : null
  subject_alternative_names = var.certificate_mode == "dns" ? var.record_names : null
  validation_method         = var.certificate_mode == "dns" ? "DNS" : null
  certificate_body          = var.certificate_mode == "imported" ? var.certificate_body_pem : null
  private_key               = var.certificate_mode == "imported" ? var.certificate_private_key : null
  certificate_chain         = var.certificate_mode == "imported" && var.certificate_chain_pem != "" ? var.certificate_chain_pem : null

  lifecycle {
    precondition {
      condition     = var.certificate_mode != "imported" || (trimspace(var.certificate_body_pem) != "" && trimspace(var.certificate_private_key) != "")
      error_message = "Imported certificates require a PEM certificate and matching private key."
    }
  }
}

resource "aws_route53_record" "validation" {
  for_each = var.certificate_mode == "dns" ? {
    for dvo in aws_acm_certificate.this.domain_validation_options :
    dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  } : {}

  zone_id = data.aws_route53_zone.this[0].zone_id
  name    = each.value.name
  type    = each.value.type
  ttl     = 60
  records = [each.value.record]
}

resource "aws_acm_certificate_validation" "this" {
  count                   = var.certificate_mode == "dns" ? 1 : 0
  certificate_arn         = aws_acm_certificate.this.arn
  validation_record_fqdns = [for record in aws_route53_record.validation : record.fqdn]
}

moved {
  from = aws_acm_certificate_validation.this
  to   = aws_acm_certificate_validation.this[0]
}
