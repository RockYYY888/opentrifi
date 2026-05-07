export function removeRecordById<T extends { id: number }>(records: T[], recordId: number): T[] {
	return records.filter((record) => record.id !== recordId);
}

export function replaceRecordById<T extends { id: number }>(records: T[], nextRecord: T): T[] {
	let hasReplacement = false;
	const updatedRecords = records.map((record) => {
		if (record.id !== nextRecord.id) {
			return record;
		}

		hasReplacement = true;
		return nextRecord;
	});

	return hasReplacement ? updatedRecords : records;
}
