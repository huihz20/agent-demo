// OptimisticPolicy ABI — JobInitialised event carries optParams with deliverable_url
export const POLICY_ABI = [
  {
    "anonymous": false,
    "inputs": [
      { "indexed": true, "internalType": "uint256", "name": "jobId", "type": "uint256" },
      { "indexed": false, "internalType": "bytes32", "name": "deliverable", "type": "bytes32" },
      { "indexed": false, "internalType": "uint64", "name": "submittedAt", "type": "uint64" },
      { "indexed": false, "internalType": "bytes", "name": "optParams", "type": "bytes" }
    ],
    "name": "JobInitialised",
    "type": "event"
  },
  {
    "inputs": [],
    "name": "disputeWindow",
    "outputs": [{ "internalType": "uint256", "name": "", "type": "uint256" }],
    "stateMutability": "view",
    "type": "function"
  }
] as const;
