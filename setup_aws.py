"""
Smart Power Monitor — AWS Infrastructure Setup
Creates all required AWS resources using boto3.

Run this ONCE to provision:
  - DynamoDB table
  - S3 bucket
  - SNS topic
  - IoT Thing + Policy + Certificate
  - Lambda functions + IAM role
  - API Gateway
  - IoT Core Rule → Lambda trigger

Usage:
    python infrastructure/setup_aws.py --region eu-central-1 --email your@email.com
"""

import boto3
import json
import time
import argparse
import zipfile
import os
import sys


def banner(msg):
    print(f"\n{'='*55}")
    print(f"  {msg}")
    print(f"{'='*55}")


def setup_dynamodb(client, table_name, region):
    banner(f"Creating DynamoDB table: {table_name}")
    try:
        client.create_table(
            TableName=table_name,
            KeySchema=[
                {"AttributeName": "device_id",  "KeyType": "HASH"},
                {"AttributeName": "timestamp",  "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "device_id",  "AttributeType": "S"},
                {"AttributeName": "timestamp",  "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
            TimeToLiveSpecification={"Enabled": True, "AttributeName": "ttl"},
        )
        print(f"  [OK] Table '{table_name}' created.")
    except client.exceptions.ResourceInUseException:
        print(f"  [SKIP] Table '{table_name}' already exists.")
    return f"arn:aws:dynamodb:{region}:*:table/{table_name}"


def setup_s3(client, bucket_name, region):
    banner(f"Creating S3 bucket: {bucket_name}")
    try:
        if region == "us-east-1":
            client.create_bucket(Bucket=bucket_name)
        else:
            client.create_bucket(
                Bucket=bucket_name,
                CreateBucketConfiguration={"LocationConstraint": region},
            )
        client.put_bucket_versioning(
            Bucket=bucket_name,
            VersioningConfiguration={"Status": "Enabled"},
        )
        print(f"  [OK] Bucket '{bucket_name}' created.")
    except client.exceptions.BucketAlreadyOwnedByYou:
        print(f"  [SKIP] Bucket '{bucket_name}' already exists.")
    return f"arn:aws:s3:::{bucket_name}"


def setup_sns(client, topic_name, email):
    banner(f"Creating SNS topic: {topic_name}")
    response = client.create_topic(Name=topic_name)
    arn = response["TopicArn"]
    print(f"  [OK] SNS topic ARN: {arn}")
    if email:
        client.subscribe(TopicArn=arn, Protocol="email", Endpoint=email)
        print(f"  [OK] Subscription sent to {email} — confirm the email to receive alerts.")
    return arn


def setup_iam_role(client, role_name):
    banner(f"Creating IAM role: {role_name}")
    trust = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }],
    }
    try:
        response = client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="Role for Smart Power Monitor Lambda functions",
        )
        arn = response["Role"]["Arn"]
        for policy in [
            "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            "arn:aws:iam::aws:policy/AmazonDynamoDBFullAccess",
            "arn:aws:iam::aws:policy/AmazonSNSFullAccess",
            "arn:aws:iam::aws:policy/AmazonS3FullAccess",
        ]:
            client.attach_role_policy(RoleName=role_name, PolicyArn=policy)
        print(f"  [OK] Role ARN: {arn}")
        print("  Waiting 15s for IAM role to propagate...")
        time.sleep(15)
        return arn
    except client.exceptions.EntityAlreadyExistsException:
        response = client.get_role(RoleName=role_name)
        print(f"  [SKIP] Role already exists.")
        return response["Role"]["Arn"]


def zip_lambda(source_file, output_zip):
    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(source_file, os.path.basename(source_file))
    with open(output_zip, "rb") as f:
        return f.read()


def setup_lambda(client, function_name, handler_path, handler_entry,
                 role_arn, env_vars, description):
    banner(f"Deploying Lambda: {function_name}")
    zip_path = f"/tmp/{function_name}.zip"
    code     = zip_lambda(handler_path, zip_path)

    kwargs = dict(
        FunctionName=function_name,
        Runtime="python3.11",
        Role=role_arn,
        Handler=handler_entry,
        Code={"ZipFile": code},
        Description=description,
        Timeout=30,
        MemorySize=256,
        Environment={"Variables": env_vars},
    )
    try:
        response = client.create_function(**kwargs)
        arn = response["FunctionArn"]
        print(f"  [OK] Created: {arn}")
    except client.exceptions.ResourceConflictException:
        response = client.update_function_code(
            FunctionName=function_name, ZipFile=code
        )
        arn = response["FunctionArn"]
        print(f"  [UPDATED] {arn}")
    return arn


def setup_iot_thing(iot_client, thing_name, policy_name, lambda_arn, region, account_id):
    banner(f"Creating IoT Thing: {thing_name}")

    try:
        iot_client.create_thing(thingName=thing_name)
        print(f"  [OK] Thing '{thing_name}' created.")
    except iot_client.exceptions.ResourceAlreadyExistsException:
        print(f"  [SKIP] Thing already exists.")

    cert_response = iot_client.create_keys_and_certificate(setAsActive=True)
    cert_arn  = cert_response["certificateArn"]
    cert_pem  = cert_response["certificatePem"]
    priv_key  = cert_response["keyPair"]["PrivateKey"]
    print(f"  [OK] Certificate created: {cert_arn[:50]}...")

    os.makedirs("certs", exist_ok=True)
    with open("certs/device-cert.pem", "w") as f: f.write(cert_pem)
    with open("certs/private-key.pem", "w") as f: f.write(priv_key)
    print("  [OK] Certificates saved to certs/")

    policy_doc = {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Action": "iot:Connect",
             "Resource": f"arn:aws:iot:{region}:{account_id}:client/{thing_name}"},
            {"Effect": "Allow", "Action": "iot:Publish",
             "Resource": f"arn:aws:iot:{region}:{account_id}:topic/power/monitor/*"},
            {"Effect": "Allow", "Action": "iot:Subscribe",
             "Resource": f"arn:aws:iot:{region}:{account_id}:topicfilter/power/monitor/*"},
            {"Effect": "Allow", "Action": "iot:Receive",
             "Resource": f"arn:aws:iot:{region}:{account_id}:topic/power/monitor/*"},
        ],
    }
    try:
        iot_client.create_policy(
            policyName=policy_name,
            policyDocument=json.dumps(policy_doc),
        )
        print(f"  [OK] Policy '{policy_name}' created.")
    except iot_client.exceptions.ResourceAlreadyExistsException:
        print(f"  [SKIP] Policy already exists.")

    iot_client.attach_policy(policyName=policy_name, target=cert_arn)
    iot_client.attach_thing_principal(thingName=thing_name, principal=cert_arn)

    endpoint = iot_client.describe_endpoint(endpointType="iot:Data-ATS")["endpointAddress"]
    print(f"  [OK] IoT Endpoint: {endpoint}")

    try:
        iot_client.create_topic_rule(
            ruleName="PowerMonitorToLambda",
            topicRulePayload={
                "sql": "SELECT * FROM 'power/monitor/data'",
                "actions": [{
                    "lambda": {"functionArn": lambda_arn}
                }],
                "ruleDisabled": False,
                "awsIotSqlVersion": "2016-03-23",
            },
        )
        print("  [OK] IoT Rule 'PowerMonitorToLambda' created.")
    except iot_client.exceptions.ResourceAlreadyExistsException:
        print("  [SKIP] IoT Rule already exists.")

    return endpoint


def main():
    parser = argparse.ArgumentParser(description="Smart Power Monitor — AWS Setup")
    parser.add_argument("--region",  default="eu-central-1")
    parser.add_argument("--email",   default="", help="Email for SNS alerts")
    parser.add_argument("--prefix",  default="power-monitor")
    args = parser.parse_args()

    region      = args.region
    prefix      = args.prefix
    session     = boto3.Session(region_name=region)
    account_id  = session.client("sts").get_caller_identity()["Account"]

    names = {
        "table":       f"{prefix}-readings",
        "bucket":      f"{prefix}-raw-{account_id}",
        "topic":       f"{prefix}-alerts",
        "role":        f"{prefix}-lambda-role",
        "lambda_iot":  f"{prefix}-iot-processor",
        "lambda_api":  f"{prefix}-api",
        "thing":       f"{prefix}-device-01",
        "iot_policy":  f"{prefix}-device-policy",
    }

    banner("Smart Power Monitor — AWS Infrastructure Setup")
    print(f"  Region:     {region}")
    print(f"  Account ID: {account_id}")
    print(f"  Prefix:     {prefix}")

    dynamo_arn = setup_dynamodb(session.client("dynamodb"), names["table"], region)
    s3_arn     = setup_s3(session.client("s3"), names["bucket"], region)
    sns_arn    = setup_sns(session.client("sns"), names["topic"], args.email)
    role_arn   = setup_iam_role(session.client("iam"), names["role"])

    env_iot = {
        "DYNAMODB_TABLE": names["table"],
        "SNS_TOPIC_ARN":  sns_arn,
        "S3_BUCKET":      names["bucket"],
    }
    env_api = {"DYNAMODB_TABLE": names["table"]}

    lambda_iot_arn = setup_lambda(
        session.client("lambda"), names["lambda_iot"],
        "lambda/handler.py", "handler.handler",
        role_arn, env_iot, "IoT data processor for Smart Power Monitor",
    )
    lambda_api_arn = setup_lambda(
        session.client("lambda"), names["lambda_api"],
        "lambda/api_handler.py", "api_handler.handler",
        role_arn, env_api, "REST API for Smart Power Monitor dashboard",
    )

    endpoint = setup_iot_thing(
        session.client("iot"), names["thing"], names["iot_policy"],
        lambda_iot_arn, region, account_id,
    )

    config = {
        "region":          region,
        "iot_endpoint":    endpoint,
        "dynamodb_table":  names["table"],
        "s3_bucket":       names["bucket"],
        "sns_topic_arn":   sns_arn,
        "lambda_iot_arn":  lambda_iot_arn,
        "lambda_api_arn":  lambda_api_arn,
    }
    with open("config.json", "w") as f:
        json.dump(config, f, indent=2)

    banner("Setup Complete!")
    print(f"  IoT Endpoint : {endpoint}")
    print(f"  DynamoDB     : {names['table']}")
    print(f"  S3 Bucket    : {names['bucket']}")
    print(f"  SNS Topic    : {sns_arn}")
    print(f"  Config saved : config.json")
    print()
    print("Next step — run the device simulator:")
    print(f"  python device/simulator.py \\")
    print(f"    --endpoint {endpoint} \\")
    print(f"    --cert certs/device-cert.pem \\")
    print(f"    --key  certs/private-key.pem \\")
    print(f"    --ca   certs/AmazonRootCA1.pem")


if __name__ == "__main__":
    main()
