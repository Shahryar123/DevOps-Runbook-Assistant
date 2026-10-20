# User-assigned managed identities: Entra ID identities owned by Azure, with
# no password or key for anyone to leak. A container that has the identity
# attached asks the platform for a token (DefaultAzureCredential does this,
# picking the identity whose client ID is in AZURE_CLIENT_ID).
#
# User-assigned (not system-assigned) because they exist before the app and
# job: their role assignments can be created and propagated first, and they
# survive an app being deleted and recreated.

resource "azurerm_user_assigned_identity" "this" {
  for_each = var.names

  name                = each.value
  resource_group_name = var.resource_group_name
  location            = var.location
  tags                = var.tags
}
