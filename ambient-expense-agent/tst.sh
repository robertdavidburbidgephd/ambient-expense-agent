#!/bin/bash
curl -s http://localhost:8081/pubsub \
  -H "Content-Type: application/json" \
  -d "{\"message\":{\"data\":\"$(printf '%s' '{"amount":45,"submitter":"bob@company.com","category":"meals","description":"Team lunch","date":"2026-04-12"}' | base64)\",\"attributes\":{\"source\":\"test\"}},\"subscription\":\"test-sub\"}"

