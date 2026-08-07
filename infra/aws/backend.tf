terraform {
  backend "s3" {
    key          = "property-hunter/main.tfstate"
    use_lockfile = true
    encrypt      = true
  }
}
