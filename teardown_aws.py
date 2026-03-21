"""
Smart Power Monitor — AWS Teardown
Deletes resources created by setup_aws.py for a given prefix.

Usage:
    python teardown_aws.py --region eu-central-1 --prefix power-monitor
"""

import argparse
import boto3
from botocore.exceptions import ClientError, NoCredentialsError


def banner(msg):
    print(f"\n{'=' * 55}")
    print(f"  {msg}")
    print(f"{'=' * 55}")


def try_call(fn, ok_msg=None, skip_msg=None):
    try:
        fn()
        if ok_msg:
            print(f"  [OK] {ok_msg}")
        return True
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "Unknown")
        if code in {
            "ResourceNotFoundException",
            "NoSuchEntity",
            "NoSuchBucket",
            "NotFoundException",
            "InvalidRequestException",
            "ResourceNotFound",
        }:
            if skip_msg:
                print(f"  [SKIP] {skip_msg}")
            return False
        raise


def empty_and_delete_bucket(s3_client, bucket_name):
    banner(f"Deleting S3 bucket: {bucket_name}")

    paginator = s3_client.get_paginator("list_object_versions")
    objects_to_delete = []
    try:
        for page in paginator.paginate(Bucket=bucket_name):
            for version in page.get("Versions", []):
                objects_to_delete.append({"Key": version["Key"], "VersionId": version["VersionId"]})
            for marker in page.get("DeleteMarkers", []):
                objects_to_delete.append({"Key": marker["Key"], "VersionId": marker["VersionId"]})
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "NoSuchBucket":
            print("  [SKIP] Bucket does not exist.")
            return
        raise

    for i in range(0, len(objects_to_delete), 1000):
        chunk = objects_to_delete[i : i + 1000]
        s3_client.delete_objects(Bucket=bucket_name, Delete={"Objects": chunk, "Quiet": True})

    if objects_to_delete:
        print(f"  [OK] Removed {len(objects_to_delete)} object versions/delete markers.")

    try_call(
        lambda: s3_client.delete_bucket(Bucket=bucket_name),
        ok_msg="Bucket deleted.",
        skip_msg="Bucket does not exist.",
    )


def delete_iot_resources(iot_client, thing_name, policy_name):
    banner(f"Deleting IoT resources for thing: {thing_name}")

    cert_arns = []
    try:
        principals = iot_client.list_thing_principals(thingName=thing_name).get("principals", [])
        cert_arns.extend(principals)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code not in {"ResourceNotFoundException", "InvalidRequestException"}:
            raise

    for cert_arn in cert_arns:
        try_call(
            lambda arn=cert_arn: iot_client.detach_thing_principal(thingName=thing_name, principal=arn),
            ok_msg=f"Detached principal from thing: {cert_arn}",
            skip_msg=f"Principal already detached: {cert_arn}",
        )
        try_call(
            lambda arn=cert_arn: iot_client.detach_policy(policyName=policy_name, target=arn),
            ok_msg=f"Detached policy from principal: {cert_arn}",
            skip_msg=f"Policy already detached from principal: {cert_arn}",
        )
        cert_id = cert_arn.split("/")[-1]
        try_call(
            lambda cid=cert_id: iot_client.update_certificate(certificateId=cid, newStatus="INACTIVE"),
            ok_msg=f"Certificate set INACTIVE: {cert_id}",
            skip_msg=f"Certificate not found: {cert_id}",
        )
        try_call(
            lambda cid=cert_id: iot_client.delete_certificate(certificateId=cid, forceDelete=True),
            ok_msg=f"Certificate deleted: {cert_id}",
            skip_msg=f"Certificate not found: {cert_id}",
        )

    try_call(
        lambda: iot_client.delete_policy(policyName=policy_name),
        ok_msg=f"Policy deleted: {policy_name}",
        skip_msg=f"Policy not found: {policy_name}",
    )
    try_call(
        lambda: iot_client.delete_thing(thingName=thing_name),
        ok_msg=f"Thing deleted: {thing_name}",
        skip_msg=f"Thing not found: {thing_name}",
    )


def delete_iot_rule_and_lambda_permission(iot_client, lambda_client, region, account_id):
    rule_name = "PowerMonitorToLambda"

    banner(f"Deleting IoT rule: {rule_name}")
    try_call(
        lambda: iot_client.delete_topic_rule(ruleName=rule_name),
        ok_msg=f"IoT rule deleted: {rule_name}",
        skip_msg=f"IoT rule not found: {rule_name}",
    )

    def remove_permission(function_name):
        try_call(
            lambda: lambda_client.remove_permission(
                FunctionName=function_name,
                StatementId="AllowIoTRuleInvoke",
            ),
            ok_msg=f"Removed IoT invoke permission from Lambda: {function_name}",
            skip_msg=f"No IoT invoke permission on Lambda: {function_name}",
        )

    return remove_permission


def main():
    parser = argparse.ArgumentParser(description="Smart Power Monitor — AWS Teardown")
    parser.add_argument("--region", default="eu-central-1")
    parser.add_argument("--prefix", default="power-monitor")
    args = parser.parse_args()

    session = boto3.Session(region_name=args.region)
    try:
        account_id = session.client("sts").get_caller_identity()["Account"]
    except NoCredentialsError:
        print("ERROR: AWS credentials not found.")
        print("Set AWS credentials and retry teardown.")
        raise SystemExit(1)
    except ClientError as e:
        print(f"ERROR: Failed to call STS GetCallerIdentity: {e}")
        raise SystemExit(1)

    names = {
        "table": f"{args.prefix}-readings",
        "bucket": f"{args.prefix}-raw-{account_id}",
        "topic": f"{args.prefix}-alerts",
        "role": f"{args.prefix}-lambda-role",
        "lambda_iot": f"{args.prefix}-iot-processor",
        "lambda_api": f"{args.prefix}-api",
        "thing": f"{args.prefix}-device-01",
        "iot_policy": f"{args.prefix}-device-policy",
    }

    banner("Smart Power Monitor — AWS Teardown")
    print(f"  Region:     {args.region}")
    print(f"  Account ID: {account_id}")
    print(f"  Prefix:     {args.prefix}")

    iot_client = session.client("iot")
    lambda_client = session.client("lambda")
    sns_client = session.client("sns")
    s3_client = session.client("s3")
    dynamodb_client = session.client("dynamodb")
    iam_client = session.client("iam")

    remove_permission = delete_iot_rule_and_lambda_permission(
        iot_client, lambda_client, args.region, account_id
    )

    banner("Deleting Lambda functions")
    for fn in [names["lambda_iot"], names["lambda_api"]]:
        remove_permission(fn)
        try_call(
            lambda function_name=fn: lambda_client.delete_function(FunctionName=function_name),
            ok_msg=f"Lambda deleted: {fn}",
            skip_msg=f"Lambda not found: {fn}",
        )

    delete_iot_resources(iot_client, names["thing"], names["iot_policy"])

    banner(f"Deleting SNS topic: {names['topic']}")
    topic_arn = f"arn:aws:sns:{args.region}:{account_id}:{names['topic']}"
    try_call(
        lambda: sns_client.delete_topic(TopicArn=topic_arn),
        ok_msg=f"SNS topic deleted: {topic_arn}",
        skip_msg=f"SNS topic not found: {topic_arn}",
    )

    empty_and_delete_bucket(s3_client, names["bucket"])

    banner(f"Deleting DynamoDB table: {names['table']}")
    try_call(
        lambda: dynamodb_client.delete_table(TableName=names["table"]),
        ok_msg=f"DynamoDB table deleted: {names['table']}",
        skip_msg=f"DynamoDB table not found: {names['table']}",
    )

    banner(f"Deleting IAM role: {names['role']}")
    attached = []
    try:
        attached = iam_client.list_attached_role_policies(RoleName=names["role"]).get("AttachedPolicies", [])
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "NoSuchEntity":
            print(f"  [SKIP] IAM role not found: {names['role']}")
        else:
            raise

    for p in attached:
        policy_arn = p["PolicyArn"]
        try_call(
            lambda arn=policy_arn: iam_client.detach_role_policy(RoleName=names["role"], PolicyArn=arn),
            ok_msg=f"Detached policy: {policy_arn}",
            skip_msg=f"Policy already detached: {policy_arn}",
        )
    try_call(
        lambda: iam_client.delete_role(RoleName=names["role"]),
        ok_msg=f"IAM role deleted: {names['role']}",
        skip_msg=f"IAM role not found: {names['role']}",
    )

    print("\nTeardown complete.")


if __name__ == "__main__":
    main()