# Bundled terraform ruleset (no plugin download, so `tflint --init` is not needed).
plugin "terraform" {
  enabled = true
  preset  = "recommended"
}
