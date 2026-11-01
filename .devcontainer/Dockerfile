# Dev container base image. The upstream image ships a Yarn apt source whose signing key is
# missing (NO_PUBKEY 62D54FD4003F6525), which breaks apt-get update in the features; drop it.
FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm
RUN rm -f /etc/apt/sources.list.d/yarn.list
