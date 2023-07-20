#!/bin/bash

SSH="ssh -o StrictHostKeyChecking=no"

# Modify hostname
change_hostname()
{
    local node="$1"
    local user="$2"
    local passwd="$3"
    sshpass -p "${passwd}" ${SSH} ${user}@${node} hostname "${user}-${node}"
}

# Get node list in the cluster.
get_cluster_list()
{
    local cluster_config=$1
    cat ${cluster_config} | python3 -c 'import sys,json;[print(node) for node in json.load(sys.stdin)["cluster"].keys()]'
}

# Get the user from node
get_node_user()
{
    local cluster_config=$1
    local node=$2
    cat ${cluster_config} | python3 -c 'import sys,json;print(json.load(sys.stdin)["cluster"]['\"${node}\"']["user"])'
}

# Get the password from node
get_node_passwd()
{
    local cluster_config=$1
    local node=$2
    cat ${cluster_config} | python3 -c 'import sys,json;print(json.load(sys.stdin)["cluster"]['\"${node}\"']["passwd"])'
}

cluster_account_config_path=$1

node_list=$(get_cluster_list ${cluster_account_config_path})
echo "-------begin--------"

for node in ${node_list}
do
    user=$(get_node_user ${cluster_account_config_path} ${node})
    passwd=$(get_node_passwd ${cluster_account_config_path} ${node})
    echo "--------------${user}@${node}----------------"
    $(change_hostname ${node} ${user} ${passwd})
    echo "---"
done