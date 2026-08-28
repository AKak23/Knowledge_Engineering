from ontology_construct.models import Chunk, LocalOntology
from ontology_construct.storage import ProgressTracker


def test_progress_tracker_records_done_and_failed_chunks(tmp_path) -> None:
    chunks = [
        Chunk(id="chunk_0001", text="a", start_char=0, end_char=1, token_count=1),
        Chunk(id="chunk_0002", text="b", start_char=2, end_char=3, token_count=1),
    ]
    tracker = ProgressTracker(tmp_path / "progress.sqlite")

    try:
        tracker.register_chunks(chunks)
        assert tracker.counts() == {"pending": 2, "done": 0, "failed": 0}

        tracker.mark_done("chunk_0001", LocalOntology(chunk_id="chunk_0001"))
        tracker.mark_failed("chunk_0002", "timeout")

        assert tracker.is_done("chunk_0001") is True
        assert tracker.status("chunk_0002") == "failed"
        assert tracker.pending_chunks(chunks) == [chunks[1]]
        assert tracker.counts() == {"pending": 0, "done": 1, "failed": 1}
        assert tracker.failed_errors() == [
            {"chunk_id": "chunk_0002", "error_message": "timeout"}
        ]
    finally:
        tracker.close()


def test_progress_tracker_can_resume_existing_db(tmp_path) -> None:
    db_path = tmp_path / "progress.sqlite"
    chunk = Chunk(id="chunk_0001", text="a", start_char=0, end_char=1, token_count=1)

    tracker = ProgressTracker(db_path)
    tracker.register_chunks([chunk])
    tracker.mark_done("chunk_0001", LocalOntology(chunk_id="chunk_0001"))
    tracker.close()

    resumed = ProgressTracker(db_path)
    try:
        assert resumed.is_done("chunk_0001") is True
        assert resumed.pending_chunks([chunk]) == []
    finally:
        resumed.close()
