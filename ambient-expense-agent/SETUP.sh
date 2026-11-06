#!/bin/bash
export GOOGLE_CLOUD_PROJECT="expense-agent-501611"
# To prevent quota limit or API enablement errors during execution,
# link your GCP project as the quota project for your ADC credentials.
gcloud auth application-default set-quota-project $GOOGLE_CLOUD_PROJECT
#export GOOGLE_GENAI_USE_VERTEXAI=True
