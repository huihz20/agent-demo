// AgenticCommerceUpgradeable ABI — functions and events used by the buyer
export const COMMERCE_ABI = [
  {
    "inputs": [
      { "internalType": "address", "name": "provider", "type": "address" },
      { "internalType": "address", "name": "evaluator", "type": "address" },
      { "internalType": "uint256", "name": "expiredAt", "type": "uint256" },
      { "internalType": "string", "name": "description", "type": "string" },
      { "internalType": "address", "name": "hook", "type": "address" }
    ],
    "name": "createJob",
    "outputs": [{ "internalType": "uint256", "name": "jobId", "type": "uint256" }],
    "stateMutability": "nonpayable",
    "type": "function"
  },
  {
    "inputs": [
      { "internalType": "uint256", "name": "jobId", "type": "uint256" },
      { "internalType": "uint256", "name": "amount", "type": "uint256" },
      { "internalType": "bytes", "name": "optParams", "type": "bytes" }
    ],
    "name": "setBudget",
    "outputs": [],
    "stateMutability": "nonpayable",
    "type": "function"
  },
  {
    "inputs": [
      { "internalType": "uint256", "name": "jobId", "type": "uint256" },
      { "internalType": "uint256", "name": "expectedBudget", "type": "uint256" },
      { "internalType": "bytes", "name": "optParams", "type": "bytes" }
    ],
    "name": "fund",
    "outputs": [],
    "stateMutability": "nonpayable",
    "type": "function"
  },
  {
    "inputs": [{ "internalType": "uint256", "name": "jobId", "type": "uint256" }],
    "name": "getJob",
    "outputs": [
      {
        "components": [
          { "internalType": "uint256", "name": "id", "type": "uint256" },
          { "internalType": "address", "name": "client", "type": "address" },
          { "internalType": "address", "name": "provider", "type": "address" },
          { "internalType": "address", "name": "evaluator", "type": "address" },
          { "internalType": "string", "name": "description", "type": "string" },
          { "internalType": "uint256", "name": "budget", "type": "uint256" },
          { "internalType": "uint256", "name": "expiredAt", "type": "uint256" },
          { "internalType": "uint8", "name": "status", "type": "uint8" },
          { "internalType": "address", "name": "hook", "type": "address" },
          { "internalType": "uint256", "name": "submittedAt", "type": "uint256" },
          { "internalType": "bytes32", "name": "deliverable", "type": "bytes32" }
        ],
        "internalType": "struct IACP.Job",
        "name": "",
        "type": "tuple"
      }
    ],
    "stateMutability": "view",
    "type": "function"
  },
  {
    "inputs": [],
    "name": "paymentToken",
    "outputs": [{ "internalType": "address", "name": "", "type": "address" }],
    "stateMutability": "view",
    "type": "function"
  },
  {
    "anonymous": false,
    "inputs": [
      { "indexed": true, "internalType": "uint256", "name": "jobId", "type": "uint256" },
      { "indexed": true, "internalType": "address", "name": "client", "type": "address" },
      { "indexed": true, "internalType": "address", "name": "provider", "type": "address" },
      { "indexed": false, "internalType": "address", "name": "evaluator", "type": "address" },
      { "indexed": false, "internalType": "uint256", "name": "expiredAt", "type": "uint256" },
      { "indexed": false, "internalType": "address", "name": "hook", "type": "address" }
    ],
    "name": "JobCreated",
    "type": "event"
  },
  {
    "anonymous": false,
    "inputs": [
      { "indexed": true, "internalType": "uint256", "name": "jobId", "type": "uint256" },
      { "indexed": true, "internalType": "address", "name": "provider", "type": "address" },
      { "indexed": false, "internalType": "bytes32", "name": "deliverable", "type": "bytes32" }
    ],
    "name": "JobSubmitted",
    "type": "event"
  }
] as const;
