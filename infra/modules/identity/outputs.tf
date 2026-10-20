output "identities" {
  description = "Per key: id (attach to an app/job), client_id (AZURE_CLIENT_ID), principal_id (role assignments)."
  value = {
    for key, identity in azurerm_user_assigned_identity.this : key => {
      id           = identity.id
      client_id    = identity.client_id
      principal_id = identity.principal_id
    }
  }
}
