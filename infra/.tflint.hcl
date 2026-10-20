# TFLint: catches what `terraform validate` can't (invalid SKUs/regions,
# deprecated syntax, unused declarations, missing version pins, naming).
#
#   cd infra
#   tflint --init                 # downloads the plugins below (once)
#   tflint --recursive            # root module and every module in modules/

config {
  # Also inspect calls to local modules (./modules/*) with their arguments.
  call_module_type = "local"
}

# Core Terraform language rules (unused variables, version pins, docs, ...).
plugin "terraform" {
  enabled = true
  preset  = "recommended"
}

# Azure-specific rules (valid SKUs, VM sizes, regions, name formats, ...).
plugin "azurerm" {
  enabled = true
  version = "0.32.0"
  source  = "github.com/terraform-linters/tflint-ruleset-azurerm"
}

rule "terraform_naming_convention" {
  enabled = true
  format  = "snake_case"
}

rule "terraform_documented_variables" {
  enabled = true
}

rule "terraform_documented_outputs" {
  enabled = true
}
